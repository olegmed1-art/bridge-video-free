from __future__ import annotations

"""Offline OracleLight -> IBM preparation, with no dispatch or calibration API.

Checks a separately exported TRAIN-only projection. Never opens a database,
archive, socket or subprocess. Hashes establish identity/consistency, not issuer
authentication. Even a consistent bundle requires a trusted coordinator event,
Diana20 release, qualified executor and protected one-use admission later.
"""

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time

from stage2b_v24 import OPENING_LEAD_OUTCOME_POLICY, opening_lead_loss
from stage2b_v25 import OOF_INPUT_POLICY, validate_oof_rows

SCHEMA = "dds-ibm-recalibration-input-v1"
MAX_ROWS = 42000
MAX_BYTES = 256 * 1024 * 1024
MAX_LINE = 64 * 1024
MAX_METADATA = 16 * 1024 * 1024
CHECK_SECONDS = 60
CODE_FILES = ("ibm_recalibration_prepare.py", "stage2b_v24.py", "stage2b_v25.py")
FILES = {"tasks", "oof", "excluded_families", "producer"}
RESOURCES = {"vcpus": 1, "memory_bytes": 2 * 1024**3, "wall_seconds": 900,
             "output_bytes": 512 * 1024**2, "heartbeat_seconds": 30}
SHA = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
CARD = re.compile(r"[SHDC][AKQJT98765432]\Z")
PREDICTION_FIELDS = {"task_id", "family_id", "task_type", "split", "fold", "heldout_fold",
                     "locked", "out_of_fold", "model_evidence_count",
                     "raw_confidence_probability", "confidence_probability", "model_backoff_level",
                     "predictor_version"}
RESULT_FIELDS = {"task_id", "family_id", "task_type", "split", "fold", "heldout_fold"}


class Blocked(ValueError):
    """Compact reason code only; never embed facts, IDs or credentials."""


def need(condition: bool, reason: str) -> None:
    if not condition:
        raise Blocked(reason)


def exact(value, keys, reason="SCHEMA"):
    need(type(value) is dict and set(value) == set(keys), reason)


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def digest(value):
    return type(value) is str and SHA.fullmatch(value) is not None


def identity(value):
    return type(value) is str and IDENTITY.fullmatch(value) is not None


def parse_json(raw: bytes):
    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def reject_constant(_):
        raise Blocked("NONFINITE_JSON")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                          parse_constant=reject_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise Blocked("INVALID_JSON") from None


def runtime_identity():
    return {"implementation": sys.implementation.name, "version": list(sys.version_info[:3])}


def verify_runtime():
    runtime = runtime_identity()
    need(runtime["implementation"] == "cpython" and runtime["version"][:2] == [3, 14],
         "RUNTIME_MISMATCH")
    return runtime


def prepare_plan(spec: dict) -> dict:
    """Validate an input specification; no I/O and no authority from JSON."""
    exact(spec, {"schema", "operation", "scope", "source_sha", "runtime_profile",
                 "code_sha256", "files", "task_count", "family_count", "fold_count",
                 "excluded_family_count", "source_archive_sha256", "source_database_sha256",
                 "source_prediction_sha256", "producer_sha", "solver"})
    need(spec["schema"] == SCHEMA and spec["operation"] == "train_recalibration_no_dds"
         and spec["scope"] == "main_train", "SCOPE")
    for key in ("source_sha", "producer_sha"):
        need(type(spec[key]) is str and COMMIT.fullmatch(spec[key]) is not None, "SOURCE_SHA")
    need(spec["runtime_profile"] == "cpython-3.14-stdlib-v1", "RUNTIME_PROFILE")
    exact(spec["code_sha256"], CODE_FILES)
    need(all(digest(v) for v in spec["code_sha256"].values()), "CODE_DIGEST")
    for key in ("source_archive_sha256", "source_database_sha256", "source_prediction_sha256"):
        need(digest(spec[key]), "SOURCE_DIGEST")
    exact(spec["solver"], {"version", "commit"})
    need(spec["solver"] == {"version": "3.0.0", "commit": "37c8a79f4c67c55d1a309ccb66dd00cb58af464a"},
         "SOLVER_PROVENANCE")
    need(integer(spec["task_count"], 1, MAX_ROWS)
         and integer(spec["family_count"], 1, spec["task_count"])
         and integer(spec["fold_count"], 2, 5)
         and integer(spec["excluded_family_count"], 1, MAX_ROWS), "COUNTS")
    exact(spec["files"], FILES)
    names = set()
    for role, item in spec["files"].items():
        exact(item, {"name", "bytes", "sha256"})
        name = item["name"]
        need(type(name) is str and re.fullmatch(r"[a-z][a-z0-9_-]{0,63}\.jsonl?", name)
             is not None and name not in names, "FILE_NAME")
        need(name.endswith(".jsonl") == (role in {"tasks", "oof"}), "FILE_TYPE")
        names.add(name)
        need(integer(item["bytes"], 1, MAX_BYTES if role == "oof" else MAX_METADATA)
             and digest(item["sha256"]), "FILE_BINDING")
    need(sum(f["bytes"] for f in spec["files"].values()) <= MAX_BYTES, "TOTAL_BYTES")
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return {"schema": "dds-ibm-recalibration-plan-v1", "spec_sha256": hashlib.sha256(canonical).hexdigest(),
            "operation": spec["operation"], "scope": spec["scope"], "source_sha": spec["source_sha"],
            "coordinator": "oracle_light", "compute_plane": "ibm",
            "github_role": "light_ci_and_coordination", "dispatch_allowed": False,
            "requested_resources": dict(RESOURCES), "resource_enforcement_verified": False,
            "solver_run": False, "fit_performed": False, "external_provenance_verified": False,
            "execution_gate": "COORDINATOR_EVENT_AFTER_DIANA20_EXPORT_REQUIRED",
            "input_bindings": json.loads(canonical),
            "outcome_policy": OPENING_LEAD_OUTCOME_POLICY, "oof_input_policy": OOF_INPUT_POLICY}


class Budget:
    def __init__(self):
        self.started = self.last = time.monotonic()
        self.bytes = 0
        self.inputs = []

    def check(self, count=0):
        now = time.monotonic()
        need(math.isfinite(now) and self.last <= now < self.started + CHECK_SECONDS, "CHECK_DEADLINE")
        self.last = now
        self.bytes += count
        # Input bindings remain capped at MAX_BYTES; each is read twice.
        need(self.bytes <= 2 * MAX_BYTES, "TOTAL_BYTES")

    def retain(self, handle, root, path, before, item):
        self.inputs.append((handle, root, path, before, item))

    def validate_bundle(self):
        # Keep the original descriptors and rehash them after all semantic
        # checks. Metadata alone misses same-size edits with restored mtime.
        for handle, root, path, before, item in self.inputs:
            self.check()
            self.validate_identity(handle, root, path, before)
            fd = handle.fileno()
            os.lseek(fd, 0, os.SEEK_SET)
            sha, total = hashlib.sha256(), 0
            while True:
                self.check()
                raw = os.read(fd, min(1024 * 1024, item["bytes"] - total + 1))
                if not raw:
                    break
                self.check(len(raw))
                total += len(raw)
                need(total <= item["bytes"], "INPUT_CHANGED")
                sha.update(raw)
            need(total == item["bytes"] and sha.hexdigest() == item["sha256"], "INPUT_CHANGED")
        for handle, root, path, before, _ in self.inputs:
            self.check()
            self.validate_identity(handle, root, path, before)

    @staticmethod
    def validate_identity(handle, root, path, before):
        for parent in (*reversed(root.parents), root):
            need(stat.S_ISDIR(no_links(parent).st_mode), "INPUT_ROOT")
        need(metadata(os.fstat(handle.fileno())) == metadata(before)
             and metadata(no_links(path)) == metadata(before), "INPUT_CHANGED")

    def close(self):
        for handle, *_ in self.inputs:
            handle.close()


def metadata(info):
    # CPython on Windows can expose creation time via stat but change time via
    # fstat for st_ctime. Identity, size, mtime and the same-descriptor digest
    # remain mandatory there; POSIX additionally compares change time.
    result = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    return result + (() if os.name == "nt" else (info.st_ctime_ns,))


def no_links(path: Path):
    info = path.lstat()
    need(not stat.S_ISLNK(info.st_mode)
         and not (getattr(info, "st_file_attributes", 0) & 0x400), "LINK_INPUT")
    return info


def verified_records(root: Path, item: dict, budget: Budget, *, jsonl: bool):
    """One descriptor for bounded reading and hashing; no extraction or writes."""
    root = root.absolute()
    for parent in (*reversed(root.parents), root):
        need(stat.S_ISDIR(no_links(parent).st_mode), "INPUT_ROOT")
    path = root / item["name"]
    before = no_links(path)
    need(stat.S_ISREG(before.st_mode), "REGULAR_FILE_REQUIRED")
    need(before.st_size == item["bytes"], "FILE_SIZE")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    handle = os.fdopen(fd, "rb")
    budget.retain(handle, root, path, before, dict(item))
    # The enclosing inspection owns descriptor closure after its final
    # bundle check, including all early rejection paths.
    need(metadata(os.fstat(handle.fileno())) == metadata(before), "INPUT_CHANGED")
    sha = hashlib.sha256()
    total = 0
    limit = MAX_LINE if jsonl else MAX_METADATA
    while True:
        budget.check()
        raw = handle.readline(limit + 1) if jsonl else handle.read(limit + 1)
        if not raw:
            break
        budget.check(len(raw))
        total += len(raw)
        need(len(raw) <= limit and total <= item["bytes"], "RECORD_LIMIT")
        sha.update(raw)
        yield parse_json(raw)
    need(total == item["bytes"] and sha.hexdigest() == item["sha256"], "FILE_DIGEST")
    need(metadata(os.fstat(handle.fileno())) == metadata(before)
         and metadata(no_links(path)) == metadata(before), "INPUT_CHANGED")


def one_record(root, item, budget):
    with closing(verified_records(root, item, budget, jsonl=False)) as stream:
        records = list(stream)
    need(len(records) == 1, "METADATA_COUNT")
    return records[0]


def ids(values):
    need(type(values) is list and len(values) <= MAX_ROWS and all(identity(x) for x in values), "FAMILY_IDS")
    result = set(values)
    need(len(result) == len(values), "DUPLICATE_FAMILY")
    return result


def check_train_artifact(spec: dict, local_root: Path) -> dict:
    """Return compact consistency evidence. Does not fit or authorize execution."""
    evidence = {"schema": "dds-ibm-train-inspection-v1", "status": "BLOCKED",
                "dispatch_allowed": False, "fit_performed": False, "solver_run": False,
                "external_provenance_verified": False, "holdout_sources_requested": False,
                "observed_runtime": runtime_identity()}
    budget = None
    try:
        verify_runtime()
        plan = prepare_plan(spec)
        evidence["spec_sha256"] = plan["spec_sha256"]
        budget = Budget()
        for name in CODE_FILES:
            with (Path(__file__).parent / name).open("rb") as handle:
                raw = handle.read(MAX_METADATA + 1)
            need(len(raw) <= MAX_METADATA, "CODE_LIMIT")
            need(hashlib.sha256(raw).hexdigest() == spec["code_sha256"][name], "CODE_DIGEST")
        root = Path(local_root)
        files = spec["files"]
        excluded = one_record(root, files["excluded_families"], budget)
        exact(excluded, {"schema", "family_ids"})
        need(excluded["schema"] == "dds-used-sealed-family-ids-v1", "EXCLUSION_SCHEMA")
        excluded_ids = ids(excluded["family_ids"])
        need(len(excluded_ids) == spec["excluded_family_count"], "EXCLUSION_COUNT")
        tasks, families = {}, {}
        with closing(verified_records(root, files["tasks"], budget, jsonl=True)) as stream:
            for task in stream:
                exact(task, {"task_id", "family_id", "task_type", "strain", "split", "heldout_fold", "legal_cards"})
                tid, family, fold = task["task_id"], task["family_id"], task["heldout_fold"]
                need(identity(tid) and identity(family) and tid not in tasks, "TASK_IDENTITY")
                need(task["split"] == "train" and family not in excluded_ids, "TRAIN_SCOPE")
                need(integer(fold, 0, spec["fold_count"] - 1)
                     and families.get(family, fold) == fold, "FAMILY_FOLD")
                need(integer(task["strain"], 0, 4) and task["task_type"] in {"contract_tricks", "opening_lead"}, "TASK_TYPE")
                cards = task["legal_cards"]
                need(type(cards) is list and all(type(c) is str and CARD.fullmatch(c) for c in cards)
                     and len(set(cards)) == len(cards), "LEGAL_CARDS")
                need(len(cards) == (13 if task["task_type"] == "opening_lead" else 0), "LEGAL_CARD_COUNT")
                tasks[tid], families[family] = task, fold
                need(len(tasks) <= spec["task_count"], "TASK_COUNT")
        need(len(tasks) == spec["task_count"] and len(families) == spec["family_count"], "TASK_COUNT")
        producer = one_record(root, files["producer"], budget)
        exact(producer, {"schema", "producer_sha", "oof_sha256", "tasks_sha256", "solver",
                         "source_archive_sha256", "source_database_sha256", "source_prediction_sha256", "folds"})
        need(producer["schema"] == "dds-oof-producer-lineage-v1"
             and producer["oof_sha256"] == files["oof"]["sha256"]
             and producer["tasks_sha256"] == files["tasks"]["sha256"], "PRODUCER_BINDING")
        for key in ("producer_sha", "solver", "source_archive_sha256", "source_database_sha256", "source_prediction_sha256"):
            need(producer[key] == spec[key], "PRODUCER_BINDING")
        need(type(producer["folds"]) is list and len(producer["folds"]) == spec["fold_count"], "PRODUCER_FOLDS")
        lineage, universe = {}, set()
        for item in producer["folds"]:
            exact(item, {"fold", "model_sha256", "training_families", "heldout_families"})
            fold = item["fold"]
            need(integer(fold, 0, spec["fold_count"] - 1) and fold not in lineage
                 and digest(item["model_sha256"]), "PRODUCER_FOLDS")
            training, heldout = ids(item["training_families"]), ids(item["heldout_families"])
            need(training and heldout and not training & heldout and not universe & heldout, "PRODUCER_LEAKAGE")
            lineage[fold] = (training, heldout)
            universe.update(heldout)
        need(len(universe) <= MAX_ROWS and not universe & excluded_ids, "PRODUCER_EXCLUSIONS")
        for training, heldout in lineage.values():
            need(training == universe - heldout, "PRODUCER_PARTITION")
        need(all(family in lineage[fold][1] for family, fold in families.items()), "PRODUCER_HELDOUT")
        seen = set()
        with closing(verified_records(root, files["oof"], budget, jsonl=True)) as stream:
            for row in stream:
                exact(row, {"task_id", "family_id", "task_type", "strain", "split", "heldout_fold",
                            "out_of_fold", "prediction", "result"})
                tid = row["task_id"]
                need(identity(tid) and tid in tasks and tid not in seen, "OOF_TASK_SET")
                task = tasks[tid]
                need(all(row[k] == task[k] and type(row[k]) is type(task[k]) for k in task if k != "legal_cards"), "OOF_TASK_BINDING")
                validate_oof_rows([row])
                prediction, result = row["prediction"], row["result"]
                lead = row["task_type"] == "opening_lead"
                prediction_fields = {"card"} if lead else {"tricks", "residual_variance", "correction"}
                result_fields = {"scores", "best_defense_tricks"} if lead else {"dds_tricks"}
                need(set(prediction) <= PREDICTION_FIELDS | prediction_fields
                     and set(result) <= RESULT_FIELDS | result_fields, "UNSUPPORTED_FACT_FIELDS")
                need(prediction.get("locked") is True and prediction.get("out_of_fold") is True, "OOF_PREDICTION")
                need(integer(prediction.get("model_evidence_count"), 0, 10**7), "MODEL_SUPPORT")
                probability = prediction.get("raw_confidence_probability")
                need(type(probability) in (int, float) and math.isfinite(probability) and 0 <= probability <= 1, "RAW_PROBABILITY")
                if "confidence_probability" in prediction:
                    value = prediction["confidence_probability"]
                    need(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1, "CONFIDENCE_PROBABILITY")
                if "predictor_version" in prediction:
                    need(identity(prediction["predictor_version"]), "PREDICTOR_VERSION")
                backoff_levels = {"baseline", "family", "broad", "coarse", "exact"}
                if lead:
                    backoff_levels.add("family_prior")
                need(prediction.get("model_backoff_level") in backoff_levels, "MODEL_BACKOFF")
                for field in ("residual_variance", "correction"):
                    value = prediction.get(field, 0)
                    need(type(value) in (int, float) and math.isfinite(value)
                         and (field != "residual_variance" or value >= 0), "MODEL_VALUE")
                if lead:
                    need(type(prediction.get("card")) is str and CARD.fullmatch(prediction["card"]) is not None, "PREDICTED_CARD")
                    scores = result.get("scores")
                    need(type(scores) is dict and set(scores) == set(task["legal_cards"]), "INCOMPLETE_SCORES")
                    need(all(integer(v, 0, 13) for v in scores.values()), "DDS_SCORE")
                    need(integer(result.get("best_defense_tricks"), 0, 13), "DDS_OPTIMUM")
                    opening_lead_loss(prediction, result)
                else:
                    need(integer(prediction.get("tricks"), 0, 13)
                         and integer(result.get("dds_tricks"), 0, 13), "DDS_TRICKS")
                seen.add(tid)
        need(seen == set(tasks), "OOF_TASK_SET")
        budget.validate_bundle()
        evidence.update(status="CONSISTENT_AWAITING_EXTERNAL_ADMISSION", reason="EXECUTION_WINDOW_CLOSED",
                        task_count=len(seen), family_count=len(families), fold_count=len(lineage),
                        bytes_read=budget.bytes, facts_sha256=files["oof"]["sha256"],
                        required_gate=plan["execution_gate"])
    except Blocked as exc:
        evidence["reason"] = str(exc)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        evidence["reason"] = "INVALID_OR_UNAVAILABLE_INPUT"
    finally:
        if budget is not None:
            budget.close()
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    try:
        runtime = verify_runtime()
        info = no_links(args.spec)
        need(stat.S_ISREG(info.st_mode) and info.st_size <= MAX_LINE, "SPEC_LIMIT")
        fd = os.open(args.spec, os.O_RDONLY | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as handle:
            need(metadata(os.fstat(handle.fileno())) == metadata(info), "SPEC_CHANGED")
            raw = handle.read(MAX_LINE + 1)
            need(metadata(os.fstat(handle.fileno())) == metadata(info)
                 and metadata(no_links(args.spec)) == metadata(info), "SPEC_CHANGED")
        need(len(raw) <= MAX_LINE, "SPEC_LIMIT")
        spec = parse_json(raw)
        if args.plan_only:
            report = dict(prepare_plan(spec), status="PREPARED_EXECUTION_BLOCKED", observed_runtime=runtime)
        else:
            need(args.root is not None, "INPUT_ROOT_REQUIRED")
            report = check_train_artifact(spec, args.root)
    except Blocked as exc:
        report = {"status": "BLOCKED", "reason": str(exc), "dispatch_allowed": False,
                  "observed_runtime": runtime_identity()}
    except (OSError, ValueError):
        report = {"status": "BLOCKED", "reason": "SPEC_UNAVAILABLE", "dispatch_allowed": False,
                  "observed_runtime": runtime_identity()}
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["status"] in {"CONSISTENT_AWAITING_EXTERNAL_ADMISSION", "PREPARED_EXECUTION_BLOCKED"} else 2)


if __name__ == "__main__":
    main()
