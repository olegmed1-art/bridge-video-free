"""Synthetic source/control tests only; no real runtime qualification claimed."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_video import comparison_sandbox as sandbox
from universal_video.comparison_producer import ComparisonProducerError


def producer_entry():
    return {"job_id": "synthetic-job", "job_hash": "d" * 64,
            "source_file_id": "synthetic_source_001", "source_version": "1",
            "source_sha256": "a" * 64, "replay_authorized": True,
            "runner_root": "/runner", "runner_commit": "e" * 40,
            "runner_sha256": "f" * 64,
            "sealed_manifest_path": "/coordinator/sealed.json",
            "sealed_manifest_sha256": "c" * 64,
            "clip_binding_path": "/coordinator/clip.json",
            "clip_binding_sha256": "b" * 64, "timeout_seconds": 10}


def fixture():
    inputs = {name: {"path": "/sealed/" + name, "sha256": str(i) * 64}
              for i, name in enumerate(("video", "reference", "profile", "sprite", "gold"), 1)}
    sealed = {"case_id": "synthetic-case", "source_offset_ms": 1500,
              "inputs": inputs,
              "baseline": {"root": "/sources/baseline", "sha": "a" * 40},
              "candidate": {"root": "/sources/candidate", "sha": "b" * 40}}
    worker = {"variant": "baseline", "job_id": "synthetic-case-baseline",
              "root": sealed["baseline"]["root"], "sha": sealed["baseline"]["sha"],
              "source_offset_ms": 1500, "inputs": {k: v for k, v in inputs.items() if k != "gold"},
              "output": "/work/raw/baseline/synthetic-case-baseline",
              "manifest_sha256": "c" * 64}
    q = {"_sealed_sha256": "c" * 64, "bwrap": {"path": "/qualified/bin/bwrap"},
         "python_target": "/usr/bin/python3",
         "_excluded_paths": ["/coordinator/qualification.json", "/coordinator/sealed.json",
                             "/coordinator/clip-receipt.json", "/ledger"]}
    return q, sealed, worker


def mounts(q, sealed, worker):
    return sandbox.worker_mounts(
        Path("/work/raw/baseline-config.json"), worker, q, sealed,
        Path("/runner/tools/recognizer_compare.py"),
        [("/qualified/runtime/usr", "/usr")], Path("/work/tmp-baseline"))


def test_missing_qualification_refuses_before_probe_or_process(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("default refusal reached OS/protected-file/process work")
    monkeypatch.setattr(sandbox, "protected_json", forbidden)
    monkeypatch.setattr(sandbox, "context", forbidden)
    monkeypatch.setattr(sandbox.subprocess, "Popen", forbidden)
    with pytest.raises(ComparisonProducerError, match="qualification unavailable"):
        sandbox.run_qualified_comparison(producer_entry())


def test_worker_view_omits_gold_seal_sibling_and_secret_stores():
    q, sealed, worker = fixture()
    ro, rw = mounts(q, sealed, worker)
    assert {src for src, dst in ro if src.startswith("/sealed/")} == {
        "/sealed/video", "/sealed/reference", "/sealed/profile", "/sealed/sprite"}
    all_sources = {src for src, dst in ro + rw}
    assert "/sealed/gold" not in all_sources
    assert "/work/raw/seal.json" not in all_sources
    assert not any("candidate" in path or "/run/secrets" in path or path == "/"
                   for path in all_sources)
    assert rw == [("/work/tmp-baseline", "/tmp"),
                  (worker["output"], worker["output"])]
    args = sandbox.command(q, ro, rw, Path("/runner/tools/recognizer_compare.py"),
                           Path("/work/raw/baseline-config.json"), worker["output"], worker["root"])
    for flag in ("--unshare-all", "--die-with-parent", "--clearenv",
                 "--disable-userns", "--assert-userns-disabled", "--cap-drop"):
        assert flag in args
    assert "--share-net" not in args
    assert args[args.index("GIT_CONFIG_VALUE_0") + 1] == "/sources/baseline"
    assert args[args.index("GIT_CONFIG_GLOBAL") + 1] == "/dev/null"
    assert "*" not in args
    assert ["--ro-bind", "/", "/"] != args[:3]
    assert args[-3:] == ["_worker", "--config", "/work/raw/baseline-config.json"]
    assert args[-6:-4] == ["-I", "-B"]


@pytest.mark.parametrize("mode", ["gold-config", "gold-in-source", "gold-in-runtime",
                                  "wrong-runtime", "wrong-seal", "wrong-offset", "extra-control"])
def test_worker_scope_refuses_gold_alias_or_binding_change(mode):
    q, sealed, worker = fixture()
    runtimes = [("/qualified/runtime/usr", "/usr")]
    if mode == "gold-config":
        worker["inputs"]["gold"] = sealed["inputs"]["gold"]
    elif mode == "gold-in-source":
        sealed["inputs"]["gold"]["path"] = "/sources/baseline/gold"
    elif mode == "gold-in-runtime":
        sealed["inputs"]["gold"]["path"] = "/qualified/runtime/usr/gold"
    elif mode == "wrong-runtime":
        worker["sha"] = "f" * 40
    elif mode == "wrong-seal":
        worker["manifest_sha256"] = "f" * 64
    elif mode == "wrong-offset":
        worker["source_offset_ms"] = 0
    else:
        worker["gold_path"] = "/sealed/gold"
    with pytest.raises(ComparisonProducerError):
        sandbox.worker_mounts(Path("/work/raw/baseline-config.json"), worker, q, sealed,
                              Path("/runner/tools/recognizer_compare.py"), runtimes,
                              Path("/work/tmp-baseline"))


@pytest.mark.parametrize("path", ["/", "//a", "/a/../b", "/a/./b", "relative",
                                 "/run/secrets/token", "/proc/1/fd/4", "/home/u/token"])
def test_unsafe_namespace_targets_refused(path):
    with pytest.raises(ComparisonProducerError):
        sandbox.safe_target(path)


@pytest.mark.parametrize("capacity,inodes", [(0, 100), (8 * 1024**2, 100), (1024, 0), (1024, 101)])
def test_workspace_requires_aggregate_byte_and_inode_bound(tmp_path, monkeypatch, capacity, inodes):
    monkeypatch.setattr(sandbox, "private_directory", lambda *a, **k: None)
    monkeypatch.setattr(sandbox, "_filesystem", lambda fd: (0x01021994, capacity))
    monkeypatch.setattr(sandbox.os, "fstatvfs", lambda fd: SimpleNamespace(f_files=inodes))
    q = {"workspace_root": str(tmp_path), "limits": {"workspace_mib": 1, "workspace_inodes": 100}}
    with pytest.raises(ComparisonProducerError, match="byte/inode"):
        sandbox.workspace(q)


def test_descendant_proof_checks_full_cgroup_not_only_cli(monkeypatch):
    observations = iter([{os.getpid(), 999999}, {os.getpid()}])
    monkeypatch.setattr(sandbox, "group_pids", lambda group: next(observations))
    monkeypatch.setattr(sandbox.time, "sleep", lambda seconds: None)
    proof = sandbox.await_no_descendants(Path("/synthetic-group"), timeout=1)
    assert proof["state"] == "ABSENT" and proof["observed_pids"] == [os.getpid()]


def test_remaining_descendant_blocks_continuation(monkeypatch):
    monkeypatch.setattr(sandbox, "group_pids", lambda group: {os.getpid(), 999999})
    with pytest.raises(ComparisonProducerError, match="descendants still present"):
        sandbox.await_no_descendants(Path("/synthetic-group"), timeout=0)


def test_same_resident_uid_refused_before_namespace_or_cgroup(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 12345)
    monkeypatch.setattr(sandbox.os, "getegid", lambda: 12345)
    q = {"qualified_at_unix": 0, "expires_at_unix": 10**12,
         "executor_uid": 12345, "executor_gid": 12345, "resident_uid": 12345}
    monkeypatch.setattr(sandbox, "cgroup_path", lambda: pytest.fail("same UID reached cgroup probe"))
    with pytest.raises(ComparisonProducerError, match="separate qualified host identity"):
        sandbox.context(q)


@pytest.mark.parametrize("mode", ["expired", "wrong-config", "invalid-uid"])
def test_unqualified_profile_refused_before_context(monkeypatch, mode):
    keys = {"schema", "qualified_at_unix", "expires_at_unix", "config_sha256",
            "executor_uid", "executor_gid", "resident_uid", "coordinator_namespaces",
            "host_user_namespace_inode", "uid_map", "gid_map",
            "resident_namespaces", "coordinator_mountinfo_sha256", "cgroup_path",
            "workspace_root", "ledger_root", "limits", "bwrap", "python_target",
            "runtime_trees", "source_trees", "qualification_evidence",
            "code_root", "source_bundle", "source_bundle_sha256"}
    q = {key: None for key in keys}
    q.update(schema=sandbox.SCHEMA, qualified_at_unix=0, expires_at_unix=10,
             config_sha256=sandbox.digest({}), executor_uid=12345,
             executor_gid=12345, resident_uid=23456, python_target="/usr/bin/python3")
    monkeypatch.setattr(sandbox.time, "time", lambda: 5)
    if mode == "expired":
        q["expires_at_unix"] = 4
    elif mode == "wrong-config":
        q["config_sha256"] = "f" * 64
    else:
        q["executor_uid"] = True
    monkeypatch.setattr(sandbox, "protected_json",
                        lambda path, **kwargs: (q, sandbox.artifacts.encoded(q)))
    monkeypatch.setattr(sandbox, "context", lambda q: pytest.fail("unqualified profile reached context"))
    with pytest.raises(ComparisonProducerError):
        sandbox.qualify({}, "/synthetic/qualification.json")

@pytest.mark.parametrize("role", ["seal", "clip", "qualification", "ledger", "sibling"])
@pytest.mark.parametrize("mount", ["source", "runtime"])
def test_readonly_tree_cannot_smuggle_protected_role(role, mount):
    q, sealed, worker = fixture()
    tree = "/sources/baseline" if mount == "source" else "/qualified/runtime/usr"
    if role == "sibling":
        sealed["candidate"]["root"] = tree + "/nested-candidate"
    else:
        index = {"qualification": 0, "seal": 1, "clip": 2, "ledger": 3}[role]
        q["_excluded_paths"][index] = tree + "/private-" + role
    with pytest.raises(ComparisonProducerError, match="protected"):
        mounts(q, sealed, worker)


def test_cgroup_subtree_includes_nested_processes(tmp_path, monkeypatch):
    nested = tmp_path / "child" / "grandchild"
    nested.mkdir(parents=True)
    (tmp_path / "cgroup.procs").write_text(str(os.getpid()) + "\n")
    (nested.parent / "cgroup.procs").write_text("")
    (nested / "cgroup.procs").write_text("999999\n")
    monkeypatch.setattr(sandbox, "cgroup_root", lambda group: tmp_path)
    assert sandbox.group_pids(Path("/synthetic")) == {os.getpid(), 999999}
    (nested / "cgroup.procs").write_text("")
    proof = sandbox.await_no_descendants(Path("/synthetic"))
    assert proof["scope"] == "CGROUP_SUBTREE"


def test_cgroup_missing_descendant_observation_refuses(tmp_path, monkeypatch):
    (tmp_path / "child").mkdir()
    (tmp_path / "cgroup.procs").write_text(str(os.getpid()) + "\n")
    monkeypatch.setattr(sandbox, "cgroup_root", lambda group: tmp_path)
    with pytest.raises(ComparisonProducerError, match="incomplete"):
        sandbox.group_pids(Path("/synthetic"))

def test_inventory_accepts_shared_helper_device_return(tmp_path, monkeypatch):
    path = tmp_path / "module.py"
    path.write_bytes(b"source")
    monkeypatch.setattr(sandbox, "private_directory", lambda *a, **k: 123)
    monkeypatch.setattr(sandbox.os, "fstat", lambda fd: SimpleNamespace(st_uid=0))
    monkeypatch.setattr(sandbox.artifacts, "checked", lambda path: None)
    monkeypatch.setattr(sandbox, "protected_read", lambda path, *args: path.read_bytes())
    records = {"module.py": {"bytes": 6,
                            "sha256": sandbox.hashlib.sha256(b"source").hexdigest()}}
    assert sandbox.inventory(str(tmp_path), records) == sandbox.digest(records)


def test_inventory_refuses_non_root_owned_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox, "private_directory", lambda *a, **k: 123)
    monkeypatch.setattr(sandbox.os, "fstat", lambda fd: SimpleNamespace(st_uid=12345))
    with pytest.raises(ComparisonProducerError, match="root-owned"):
        sandbox.root_owned_directory(tmp_path)

@pytest.mark.parametrize("continuity", ["unchanged", "profile-swap",
                                      "qualification-byte-swap", "evidence-byte-swap"])
def test_qualified_api_passes_only_paths_to_worker_exclusions(tmp_path, monkeypatch, continuity):
    q, sealed, worker = fixture()
    q.update(workspace_root=str(tmp_path), ledger_root=str(tmp_path / "ledger"),
             source_bundle={"files": {"source.py": "synthetic-inventory"}},
             qualification_evidence={"path": "/coordinator/proof.json"},
             expires_at_unix=10**12,
             runtime_trees=[{"target": "/usr", "root": "/qualified/runtime/usr",
                             "files": {"bin/git": {}}}])
    (tmp_path / "ledger").mkdir()
    config = producer_entry()
    qualification_calls = []
    initial_observation = {"qualification_sha256": "a" * 64,
                           "evidence_sha256": "b" * 64,
                           "profile_sha256": "c" * 64}
    def qualify(*args):
        qualification_calls.append(True)
        effective_q, observation = dict(q), dict(initial_observation)
        if len(qualification_calls) > 1:
            if continuity == "profile-swap":
                effective_q["expires_at_unix"] -= 1
            elif continuity == "qualification-byte-swap":
                observation["qualification_sha256"] = "d" * 64
            elif continuity == "evidence-byte-swap":
                observation["evidence_sha256"] = "e" * 64
        return effective_q, observation
    monkeypatch.setattr(sandbox, "qualify", qualify)
    monkeypatch.setattr(sandbox, "_preflight", lambda config: sealed)
    runner = Path("/runner/tools/recognizer_compare.py")
    monkeypatch.setattr(sandbox, "trees", lambda *a: (runner, []))
    monkeypatch.setattr(sandbox, "workspace", lambda q: tmp_path)
    monkeypatch.setattr(sandbox, "private_directory", lambda *a, **k: 123)
    monkeypatch.setattr(sandbox, "context", lambda q: {})
    writes = []
    monkeypatch.setattr(sandbox, "atomic_json", lambda path, value: writes.append(value))
    module = SimpleNamespace(verify_checkout=lambda *a: None)
    spec = SimpleNamespace(loader=SimpleNamespace(exec_module=lambda module: None))
    monkeypatch.setattr(sandbox.importlib.util, "spec_from_file_location", lambda *a: spec)
    monkeypatch.setattr(sandbox.importlib.util, "module_from_spec", lambda spec: module)
    observed = []
    def launch(path, sealed, effective_q, runner, mounts, attempt, kwargs):
        observed.extend(effective_q["_excluded_paths"])
        assert all(isinstance(path, str) and path.startswith("/") for path in observed)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(sandbox, "launch_worker", launch)
    def compare(manifest, sha, raw, **kwargs):
        module.launch_worker(["python", "-I", "-B", str(runner), "_worker",
                              "--config", str(raw / "baseline-config.json")])
        return 0
    module.compare = compare
    if continuity != "unchanged":
        with pytest.raises(ComparisonProducerError, match="admitted qualification/evidence changed"):
            sandbox.run_qualified_comparison(
                config, qualification_file="/coordinator/qualification.json")
        assert len(qualification_calls) == 2
        assert not any(value.get("state") == "CAPTURED_UNSCORED" for value in writes)
        return
    result = sandbox.run_qualified_comparison(config,
                                              qualification_file="/coordinator/qualification.json")
    assert result == tmp_path / ("comparison-" + "d" * 64) / "raw"
    assert "/coordinator/sealed.json" in observed
    assert "/coordinator/clip.json" in observed

def forbid_execution_side_effects(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid authorization/schema reached qualification or side effects")
    for name in ("qualify", "protected_json", "context", "_preflight",
                 "trees", "workspace", "atomic_json"):
        monkeypatch.setattr(sandbox, name, forbidden)
    monkeypatch.setattr(sandbox.subprocess, "Popen", forbidden)
    monkeypatch.setattr(sandbox.importlib.util, "spec_from_file_location", forbidden)
    monkeypatch.setattr(sandbox.Path, "mkdir", forbidden)


@pytest.mark.parametrize("authorization", [False, None, 0, 1, "", "true", "false", [], {}])
def test_explicit_denial_or_non_boolean_refuses_before_qualification(monkeypatch, authorization):
    config = producer_entry()
    config["replay_authorized"] = authorization
    forbid_execution_side_effects(monkeypatch)
    with pytest.raises(ComparisonProducerError, match="exact authorized producer entry"):
        sandbox.run_qualified_comparison(config, qualification_file="/coordinator/valid-proof.json")


def test_missing_authorization_refuses_before_qualification(monkeypatch):
    config = producer_entry()
    del config["replay_authorized"]
    forbid_execution_side_effects(monkeypatch)
    with pytest.raises(ComparisonProducerError, match="exact authorized producer entry"):
        sandbox.run_qualified_comparison(config, qualification_file="/coordinator/valid-proof.json")


@pytest.mark.parametrize("mode", ["not-object", "missing-field", "unknown-field",
                                  "timeout-bool", "timeout-zero", "timeout-string",
                                  "invalid-commit", "invalid-source-hash",
                                  "relative-path", "path-object", "empty-job",
                                  "numeric-version", "zero-version"])
def test_complete_entry_schema_refuses_before_qualification(monkeypatch, mode):
    config = producer_entry()
    if mode == "not-object":
        config = []
    elif mode == "missing-field":
        del config["source_file_id"]
    elif mode == "unknown-field":
        config["qualification_override"] = True
    else:
        field, value = {
            "timeout-bool": ("timeout_seconds", True),
            "timeout-zero": ("timeout_seconds", 0),
            "timeout-string": ("timeout_seconds", "10"),
            "invalid-commit": ("runner_commit", "bad"),
            "invalid-source-hash": ("source_sha256", "bad"),
            "relative-path": ("runner_root", "runner"),
            "path-object": ("runner_root", Path("/runner")),
            "empty-job": ("job_id", ""),
            "numeric-version": ("source_version", 1),
            "zero-version": ("source_version", "0"),
        }[mode]
        config[field] = value
    forbid_execution_side_effects(monkeypatch)
    with pytest.raises(RuntimeError):
        sandbox.run_qualified_comparison(config, qualification_file="/coordinator/valid-proof.json")


def test_authorized_complete_entry_reaches_qualification_with_snapshot(monkeypatch):
    config = producer_entry()
    class ReachedQualification(Exception):
        pass
    def qualify(effective, path):
        assert effective == config and effective is not config
        assert effective["replay_authorized"] is True
        raise ReachedQualification
    monkeypatch.setattr(sandbox, "qualify", qualify)
    with pytest.raises(ReachedQualification):
        sandbox.run_qualified_comparison(config, qualification_file="/coordinator/proof.json")

def host_profile():
    return {"host_user_namespace_inode": 123456,
            "uid_map": [[0, 0, 4294967295]], "gid_map": [[0, 0, 4294967295]],
            "executor_uid": 12345, "resident_uid": 23456}


@pytest.mark.parametrize("mode", ["wrong-user-namespace", "same-host-identity",
                                  "missing-map", "shifted-uid", "shifted-gid",
                                  "split-map", "boolean-map", "observed-shift",
                                  "observed-gid-shift", "unreadable-map"])
def test_ambiguous_or_same_host_mapping_refuses(monkeypatch, mode):
    q = host_profile()
    ns = {"user": 123456}
    if mode == "wrong-user-namespace":
        ns["user"] = 999999
    elif mode == "same-host-identity":
        q["resident_uid"] = q["executor_uid"]
    elif mode == "missing-map":
        del q["uid_map"]
    elif mode == "shifted-uid":
        q["uid_map"] = [[0, 100000, 65536]]
    elif mode == "shifted-gid":
        q["gid_map"] = [[0, 100000, 65536]]
    elif mode == "split-map":
        q["uid_map"] = [[0, 0, 1], [1, 100000, 65535]]
    elif mode == "boolean-map":
        q["uid_map"] = [[False, 0, 4294967295]]
    def read(path, *args, **kwargs):
        if mode == "unreadable-map":
            raise PermissionError
        if mode == "observed-shift" and path.name == "uid_map":
            return "0 100000 65536\n"
        if mode == "observed-gid-shift" and path.name == "gid_map":
            return "0 100000 65536\n"
        return "0 0 4294967295\n"
    monkeypatch.setattr(sandbox.Path, "read_text", read)
    with pytest.raises(ComparisonProducerError, match="host"):
        sandbox.host_identity(q, ns)


def test_initial_host_mapping_observed_not_numeric_namespace_uid_only(monkeypatch):
    reads = []
    def read(path, *args, **kwargs):
        reads.append(str(path))
        return "0 0 4294967295\n"
    monkeypatch.setattr(sandbox.Path, "read_text", read)
    sandbox.host_identity(host_profile(), {"user": 123456})
    assert reads == ["/proc/self/uid_map", "/proc/self/gid_map"]


def test_context_rejects_wrong_host_namespace_before_cgroup(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.os, "geteuid", lambda: 12345)
    monkeypatch.setattr(sandbox.os, "getegid", lambda: 12345)
    monkeypatch.setattr(sandbox.os, "stat", lambda path: SimpleNamespace(st_ino=999999))
    monkeypatch.setattr(sandbox, "cgroup_path", lambda: pytest.fail("mapping gap reached cgroup"))
    q = host_profile()
    q.update(qualified_at_unix=0, expires_at_unix=10**12, executor_gid=12345)
    with pytest.raises(ComparisonProducerError, match="initial host user namespace"):
        sandbox.context(q)
