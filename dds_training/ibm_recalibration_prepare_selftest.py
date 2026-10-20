from __future__ import annotations

"""Small synthetic fixtures only: no corpus, fit, DDS or remote transport."""

import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import ibm_recalibration_prepare as adapter
from stage2b_v24 import rank_opening_leads


def write(path, value, *, jsonl=False):
    text = ("".join(json.dumps(row, sort_keys=True) + "\n" for row in value) if jsonl
            else json.dumps(value, sort_keys=True) + "\n")
    path.write_text(text, encoding="utf-8", newline="\n")


def binding(path):
    raw = path.read_bytes()
    return {"name": path.name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def fixture():
    tasks, rows = [], []
    cards = ["H" + rank for rank in "AKQJT98765432"]
    for fold in range(2):
        for kind in ("contract_tricks", "opening_lead"):
            tid, family = f"task-{fold}-{kind}", f"family-{fold}"
            common = {"task_id": tid, "family_id": family, "task_type": kind,
                      "strain": 4, "split": "train", "heldout_fold": fold}
            tasks.append(dict(common, legal_cards=cards[:] if kind == "opening_lead" else []))
            prediction = {"task_id": tid, "locked": True, "out_of_fold": True,
                          "model_evidence_count": 100, "model_backoff_level": "exact",
                          "raw_confidence_probability": 0.6}
            result = {"task_id": tid}
            if kind == "opening_lead":
                prediction["card"] = cards[0]
                result.update(scores={c: i % 7 for i, c in enumerate(cards)}, best_defense_tricks=6)
            else:
                prediction["tricks"], result["dds_tricks"] = 7, 8
            rows.append(dict(common, out_of_fold=True, prediction=prediction, result=result))
    excluded = {"schema": "dds-used-sealed-family-ids-v1", "family_ids": ["sealed-family"]}
    producer = {"schema": "dds-oof-producer-lineage-v1", "folds": [
        {"fold": f, "model_sha256": str(f + 1) * 64,
         "training_families": [f"family-{1-f}"], "heldout_families": [f"family-{f}"]}
        for f in range(2)]}
    return {"tasks": tasks, "oof": rows, "excluded_families": excluded, "producer": producer}


def save_bundle(root, data):
    spec = {"schema": adapter.SCHEMA, "operation": "train_recalibration_no_dds", "scope": "main_train",
            "source_sha": "a" * 40, "producer_sha": "b" * 40,
            "runtime_profile": "cpython-3.14-stdlib-v1", "task_count": 4, "family_count": 2,
            "fold_count": 2, "excluded_family_count": 1,
            "source_archive_sha256": "c" * 64, "source_database_sha256": "d" * 64,
            "source_prediction_sha256": "e" * 64,
            "solver": {"version": "3.0.0", "commit": "37c8a79f4c67c55d1a309ccb66dd00cb58af464a"},
            "code_sha256": {name: hashlib.sha256((Path(adapter.__file__).parent / name).read_bytes()).hexdigest()
                            for name in adapter.CODE_FILES}, "files": {}}
    for role in ("tasks", "oof", "excluded_families"):
        path = root / (role + (".jsonl" if role in {"tasks", "oof"} else ".json"))
        write(path, data[role], jsonl=role in {"tasks", "oof"})
        spec["files"][role] = binding(path)
    producer = deepcopy(data["producer"])
    producer.update({k: spec[k] for k in ("producer_sha", "solver", "source_archive_sha256",
                                        "source_database_sha256", "source_prediction_sha256")})
    producer.update(oof_sha256=spec["files"]["oof"]["sha256"], tasks_sha256=spec["files"]["tasks"]["sha256"])
    write(root / "producer.json", producer)
    spec["files"]["producer"] = binding(root / "producer.json")
    return spec


def rejected(spec, root):
    report = adapter.check_train_artifact(spec, root)
    assert report["status"] == "BLOCKED", report
    assert report["dispatch_allowed"] is False and report["fit_performed"] is False
    assert not any(key in report for key in ("prediction", "result", "task_id", "family_ids"))
    return report["reason"]


def fixture_checks():
    cases = 0
    with tempfile.TemporaryDirectory(prefix="dds-adapter-") as temporary:
        root = Path(temporary)
        data = fixture()
        spec = save_bundle(root, data)
        original = deepcopy(spec)
        before = {p.name: p.read_bytes() for p in root.iterdir()}
        plan = adapter.prepare_plan(spec)
        assert spec == original and plan["dispatch_allowed"] is False
        assert plan["requested_resources"] == adapter.RESOURCES
        assert plan["requested_resources"]["wall_seconds"] == 900
        assert plan["resource_enforcement_verified"] is False
        with patch("subprocess.Popen", side_effect=AssertionError("process forbidden")), \
             patch("socket.socket", side_effect=AssertionError("network forbidden")), \
             patch("stage2b_v25.recalibrate_oof_rows", side_effect=AssertionError("fit forbidden")):
            report = adapter.check_train_artifact(spec, root)
        assert report["status"] == "CONSISTENT_AWAITING_EXTERNAL_ADMISSION", report
        assert report["task_count"] == 4 and report["family_count"] == 2
        assert report["external_provenance_verified"] is False and report["dispatch_allowed"] is False
        assert report["observed_runtime"]["implementation"] == "cpython"
        assert report["observed_runtime"]["version"][:2] == [3, 14]
        assert report["bytes_read"] == 2 * sum(item["bytes"] for item in spec["files"].values())
        assert before == {p.name: p.read_bytes() for p in root.iterdir()}

        ranked = rank_opening_leads(
            [{"card": "HA", "levels": [("family", "opening_lead_nt")]}],
            family="opening_lead_nt", model={})
        assert ranked["model_backoff_level"] == "family_prior"
        prior_data = fixture()
        prior_lead = next(row["prediction"] for row in prior_data["oof"] if row["task_type"] == "opening_lead")
        prior_lead.update(card=ranked["card"], model_backoff_level=ranked["model_backoff_level"],
                          model_evidence_count=ranked["support_count"],
                          raw_confidence_probability=ranked["raw_confidence_probability"])
        assert adapter.check_train_artifact(save_bundle(root, prior_data), root)["status"] == "CONSISTENT_AWAITING_EXTERNAL_ADMISSION"
        for kind, level in (("contract_tricks", "family_prior"), ("opening_lead", "unknown_prior")):
            changed = fixture()
            next(row["prediction"] for row in changed["oof"] if row["task_type"] == kind)["model_backoff_level"] = level
            assert rejected(save_bundle(root, changed), root) == "MODEL_BACKOFF"
            cases += 1
        spec = save_bundle(root, fixture())

        for key, value in (("approved", True), ("coordinator_receipt", {"approved": True}),
                           ("dispatch_allowed", True), ("resources", {"vcpus": 10})):
            changed = deepcopy(spec)
            changed[key] = value
            rejected(changed, root)
            cases += 1
        for key, value in (("scope", "sealed_test"), ("operation", "dds_evaluate"),
                           ("source_sha", "main"), ("task_count", True), ("task_count", 42001),
                           ("fold_count", 1), ("runtime_profile", "latest")):
            changed = deepcopy(spec)
            changed[key] = value
            rejected(changed, root)
            cases += 1
        changed = deepcopy(spec)
        changed["files"]["oof"]["name"] = "../oof.jsonl"
        rejected(changed, root)
        cases += 1
        changed = deepcopy(spec)
        changed["code_sha256"]["stage2b_v25.py"] = "0" * 64
        assert rejected(changed, root) == "CODE_DIGEST"
        cases += 1

        mutations = [
            lambda d: d["oof"][0].pop("out_of_fold"),
            lambda d: d["oof"][0].update(out_of_fold="true"),
            lambda d: d["oof"][0].update(split="validation"),
            lambda d: d["oof"][0].update(task_id="different-but-same-count"),
            lambda d: d["oof"].append(deepcopy(d["oof"][0])),
            lambda d: d["oof"].pop(),
            lambda d: d["oof"][0]["prediction"].update(locked=False),
            lambda d: d["oof"][0]["prediction"].update(out_of_fold=False),
            lambda d: d["oof"][0]["prediction"].update(raw_confidence_probability=float("nan")),
            lambda d: d["oof"][0]["prediction"].update(raw_confidence_probability=1.1),
            lambda d: d["oof"][0]["prediction"].update(model_evidence_count=True),
            lambda d: d["oof"][0]["prediction"].update(residual_variance=-1),
            lambda d: d["oof"][0]["result"].update(split="sealed_test"),
            lambda d: d["oof"][0]["result"].update(dds_tricks=True),
            lambda d: d["oof"][0]["result"].update(deal="UNREQUESTED_PAYLOAD"),
            lambda d: d["oof"][0]["result"].update(scores={"UNREQUESTED_PAYLOAD": {"split": "sealed_test", "answer": 13}}),
            lambda d: d["oof"][1]["prediction"].update(tricks={"split": "sealed_test"}),
            lambda d: d["oof"][0]["prediction"].update(predictor_version={"answer": 13}),
            lambda d: d["oof"][0]["prediction"].update(predictor_version="v" * 129),
            lambda d: d["oof"][0]["prediction"].update(confidence_probability=2),
            lambda d: d["oof"][0]["prediction"].update(confidence_probability=True),
            lambda d: d["oof"][1]["prediction"].update(residual_variance={"answer": 13}),
            lambda d: d["oof"][1]["result"].update(dds_tricks=7),
            lambda d: d["oof"][1]["result"]["scores"].pop("HA"),
            lambda d: d["oof"][1]["result"].update(best_defense_tricks=7),
            lambda d: d["oof"][1]["result"]["scores"].update(HA=14),
            lambda d: d["tasks"][0].update(split="sealed_test"),
            lambda d: d["tasks"][1].update(heldout_fold=1),
            lambda d: d["tasks"][1]["legal_cards"].pop(),
            lambda d: d["tasks"][0].update(task_id=d["tasks"][1]["task_id"]),
            lambda d: d["excluded_families"].update(family_ids=["family-0"]),
            lambda d: d["producer"]["folds"][0]["training_families"].append("family-0"),
            lambda d: d["producer"]["folds"][0].update(model_sha256="not-a-hash"),
            lambda d: d["producer"]["folds"][0].update(training_families=["unknown-family"]),
            lambda d: d["producer"]["folds"].pop(),
        ]
        for mutate in mutations:
            changed = fixture()
            mutate(changed)
            rejected(save_bundle(root, changed), root)
            cases += 1

        spec = save_bundle(root, fixture())
        (root / "oof.jsonl").write_bytes(b"changed\n")
        reason = rejected(spec, root)
        assert reason == "FILE_SIZE", reason
        cases += 1
        for raw in (b'{"duplicate":1,"duplicate":2}\n', b'\xff\n', b' ' * (adapter.MAX_LINE + 1)):
            spec = save_bundle(root, fixture())
            (root / "oof.jsonl").write_bytes(raw)
            spec["files"]["oof"] = binding(root / "oof.jsonl")
            producer = json.loads((root / "producer.json").read_text())
            producer["oof_sha256"] = spec["files"]["oof"]["sha256"]
            write(root / "producer.json", producer)
            spec["files"]["producer"] = binding(root / "producer.json")
            rejected(spec, root)
            cases += 1
        spec = save_bundle(root, fixture())
        spec["files"]["tasks"]["sha256"] = "0" * 64
        assert rejected(spec, root) == "FILE_DIGEST"
        cases += 1
        spec = save_bundle(root, fixture())
        with patch.object(adapter.time, "monotonic", side_effect=[0.0, 60.0]):
            assert rejected(spec, root) == "CHECK_DEADLINE"
        cases += 1

        original_parse = adapter.parse_json
        changed_once = False
        def change_input(raw):
            nonlocal changed_once
            result = original_parse(raw)
            if not changed_once:
                changed_once = True
                path = root / "excluded_families.json"
                os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
            return result
        with patch.object(adapter, "parse_json", side_effect=change_input):
            assert rejected(spec, root) == "INPUT_CHANGED"
        cases += 1

        # Mutate an earlier input during OOF parsing, after its first reader
        # finished. Restoring size/mtime must not defeat the final digest pass.
        for role in ("tasks", "excluded_families", "producer"):
            for mutation in ("rewrite", "touch", "replace", "delete"):
                spec = save_bundle(root, fixture())
                changed_once = False
                prevented = False
                retained = []
                original_retain = adapter.Budget.retain
                def record_handle(budget, handle, *args):
                    retained.append(handle)
                    return original_retain(budget, handle, *args)
                def late_change(raw):
                    nonlocal changed_once, prevented
                    value = original_parse(raw)
                    if isinstance(value, dict) and "prediction" in value and not changed_once:
                        changed_once = True
                        path = root / spec["files"][role]["name"]
                        before_stat = path.stat()
                        before_bytes = path.read_bytes()
                        replacement = root / "replacement.tmp"
                        try:
                            if mutation == "rewrite":
                                path.write_bytes(before_bytes[:-1] + b" ")
                                os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
                            elif mutation == "touch":
                                os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns + 1_000_000_000))
                            else:
                                if mutation == "replace":
                                    replacement.write_bytes(before_bytes)
                                os.replace(replacement, path) if mutation == "replace" else path.unlink()
                        except PermissionError:
                            assert os.name == "nt" and path.read_bytes() == before_bytes
                            prevented = True
                        if replacement.exists():
                            replacement.unlink()
                    return value
                with patch.object(adapter, "parse_json", side_effect=late_change), \
                     patch.object(adapter.Budget, "retain", record_handle):
                    report = adapter.check_train_artifact(spec, root)
                assert changed_once and retained and all(handle.closed for handle in retained)
                assert report["status"] == ("CONSISTENT_AWAITING_EXTERNAL_ADMISSION" if prevented else "BLOCKED"), report
                cases += not prevented

        # Attack an earlier file after its second hash, during the next file's
        # rehash. Windows must prevent the write; POSIX must detect its ctime.
        for index, role in enumerate(("excluded_families", "tasks", "producer")):
            spec = save_bundle(root, fixture())
            retained = []
            attempted = prevented = False
            original_read = os.read
            def record_late_handle(budget, handle, *args):
                retained.append(handle)
                return original_retain(budget, handle, *args)
            def post_hash_write(fd, size):
                nonlocal attempted, prevented
                raw = original_read(fd, size)
                if fd == retained[index + 1].fileno() and not attempted:
                    attempted = True
                    path = root / spec["files"][role]["name"]
                    before_stat, before_bytes = path.stat(), path.read_bytes()
                    try:
                        path.write_bytes(before_bytes[:-1] + b" ")
                        os.utime(path, ns=(before_stat.st_atime_ns, before_stat.st_mtime_ns))
                    except PermissionError:
                        assert os.name == "nt" and path.read_bytes() == before_bytes
                        prevented = True
                return raw
            with patch.object(adapter.Budget, "retain", record_late_handle), \
                 patch.object(adapter.os, "read", side_effect=post_hash_write):
                report = adapter.check_train_artifact(spec, root)
            assert attempted and all(handle.closed for handle in retained)
            assert report["status"] == ("CONSISTENT_AWAITING_EXTERNAL_ADMISSION" if prevented else "BLOCKED"), report
            assert prevented == (os.name == "nt")
            cases += not prevented

        if os.name == "nt":
            import mmap
            for role in ("tasks", "oof", "excluded_families", "producer"):
                spec = save_bundle(root, fixture())
                path = root / spec["files"][role]["name"]
                with path.open("r+b"):
                    assert rejected(spec, root) == "INVALID_OR_UNAVAILABLE_INPUT"
                with path.open("r+b") as writer:
                    mapping = mmap.mmap(writer.fileno(), 0, access=mmap.ACCESS_WRITE)
                try:
                    assert rejected(spec, root) == "INVALID_OR_UNAVAILABLE_INPUT"
                finally:
                    mapping.close()
                cases += 2

        if hasattr(os, "mkfifo"):
            spec = save_bundle(root, fixture())
            (root / "oof.jsonl").unlink()
            os.mkfifo(root / "oof.jsonl")
            assert rejected(spec, root) == "REGULAR_FILE_REQUIRED"
            (root / "oof.jsonl").unlink()
            cases += 1
        spec = save_bundle(root, fixture())
        link = root / "linked.jsonl"
        try:
            link.symlink_to(root / "oof.jsonl")
        except OSError:
            pass  # Windows without symlink privilege; Linux CI exercises this.
        else:
            spec["files"]["oof"]["name"] = link.name
            assert rejected(spec, root) == "LINK_INPUT"
            link.unlink()
            cases += 1

        spec = save_bundle(root, fixture())
        write(root / "spec.json", spec)
        command = [sys.executable, str(Path(adapter.__file__)), "--spec", str(root / "spec.json")]
        for args, expected in ((["--plan-only"], "PREPARED_EXECUTION_BLOCKED"),
                               (["--root", str(root)], "CONSISTENT_AWAITING_EXTERNAL_ADMISSION")):
            result = subprocess.run(command + args, capture_output=True, text=True, timeout=10)
            output = json.loads(result.stdout)
            native_supported = (sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 14)
                                and sys.version_info[3:] == ("final", 0))
            assert result.returncode == (0 if native_supported else 2), result.stderr
            assert output["status"] == (expected if native_supported else "BLOCKED")
            if not native_supported:
                assert output["reason"] == "RUNTIME_MISMATCH"
            assert output["dispatch_allowed"] is False
        write(root / "spec.json", dict(spec, approved=True))
        result = subprocess.run(command + ["--plan-only"], capture_output=True, text=True, timeout=10)
        assert result.returncode == 2 and json.loads(result.stdout)["dispatch_allowed"] is False

    source = Path(adapter.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imports |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imports & {"subprocess", "socket", "sqlite3", "tarfile", "zipfile", "requests", "ibm_trial_executor", "dds"}
    assert "recalibrate_oof_rows" not in source and not hasattr(adapter, "dispatch")
    return cases


def main():
    native_runtime = adapter.runtime_identity()
    cases = 0
    for implementation, version in (("cpython", (3, 12, 14, "final", 0)), ("cpython", (3, 13, 0, "final", 0)),
                                     ("cpython", (3, 15, 0, "final", 0)), ("pypy", (3, 14, 0, "final", 0)),
                                     ("cpython", (3, 14, 0, "alpha", 1)), ("cpython", (3, 14, 0, "beta", 2)),
                                     ("cpython", (3, 14, 0, "candidate", 2))):
        observed = SimpleNamespace(implementation=SimpleNamespace(name=implementation), version_info=version)
        with patch.object(adapter, "sys", observed), \
             patch.object(Path, "open", side_effect=AssertionError("input read before runtime gate")), \
             patch.object(adapter.os, "open", side_effect=AssertionError("input opened before runtime gate")):
            report = adapter.check_train_artifact({}, Path("unavailable"))
            assert report["status"] == "BLOCKED" and report["reason"] == "RUNTIME_MISMATCH"
            assert report["observed_runtime"] == {"implementation": implementation, "version": list(version[:3]),
                                                   "releaselevel": version[3], "serial": version[4]}
            cases += 1
    simulated = (native_runtime["implementation"] != "cpython" or native_runtime["version"][:2] != [3, 14]
                 or native_runtime["releaselevel"] != "final" or native_runtime["serial"] != 0)
    if simulated:
        with patch.object(adapter, "runtime_identity", return_value={"implementation": "cpython", "version": [3, 14, 0],
                                                                   "releaselevel": "final", "serial": 0}):
            cases += fixture_checks()
    else:
        cases += fixture_checks()
    print(json.dumps({"ok": True, "negative_cases": cases, "synthetic_train_rows": 4,
                      "actual_runtime": native_runtime, "functional_runtime_simulated": simulated,
                      "dispatch_allowed": False, "fit_performed": False, "dds_called": False}, indent=2))


if __name__ == "__main__":
    main()
