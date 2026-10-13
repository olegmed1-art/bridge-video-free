"""Offline fixture checks; actual SQLSTATE/ACL/rollback evidence comes from PG18."""
from contextlib import nullcontext
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "database/tests/fixed_subset_admission" / (name+".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load("runtime_fixture")
install = load("managed_owner_install")


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    spy = Mock(side_effect=AssertionError("offline fixture must not connect"))
    monkeypatch.setattr(runtime.psycopg, "connect", spy)
    return spy


def claim():
    return dict(source_file_id="synthetic-source-file", source_name="lesson.mp4", source_mime_type="video/mp4",
        source_size_bytes=2000000, source_folder_id="synthetic-source-folder", source_checksum=None,
        output_folder_id="synthetic-output-folder", stable_job_key="f"*32, algorithm_revision=runtime.REVISION)


def test_default_and_missing_sentinel_cannot_connect(monkeypatch, capsys, no_connections):
    assert runtime.main(["--scenario", "042"]) == 0
    assert json.loads(capsys.readouterr().out)["sql_execution"] is False
    monkeypatch.delenv("FIXED_SUBSET_DISPOSABLE_PREFLIGHT", raising=False)
    with pytest.raises(AssertionError, match="disposable"):
        runtime.main(["--scenario", "043", "--execute"])
    no_connections.assert_not_called()


@pytest.mark.parametrize("label,count,index", [("042", 2, 0), ("042-retry", 1, 0), ("042-bad", 1, 0), ("043", 3, 1)])
def test_synthetic_contracts_preserve_revision_and_canary(label, count, index):
    raw = runtime.raw_sources(label, count)
    contract = runtime.contract_for(raw, index)
    assert len(contract["files"]) == count
    assert contract["canary_source_file_id"] == raw[index]["id"]
    assert contract["execution_allowed"] is False
    assert all(f["version"] == "17" for f in contract["files"])
    assert contract["queue_manifest_sha256"] == runtime.digest(contract["enqueue_manifest"])


@pytest.mark.parametrize("pdf,ai,allowed", runtime.SIZE_CASES)
def test_boundary_payloads_bind_exact_sizes_and_recomputed_hashes(pdf, ai, allowed):
    output = runtime.terminal_output(claim(), pdf, ai)
    assert [a["size_bytes"] for a in output["artifact_manifest"]["artifacts"]] == [pdf, ai]
    assert output["artifact_manifest_sha256"] == runtime.digest(output["artifact_manifest"])
    receipt = deepcopy(output["terminal_receipt"])
    evidence = receipt.pop("evidence_sha256")
    assert evidence == runtime.digest(receipt) == output["terminal_evidence_sha256"]


def test_invalid_case_inventory_and_semantic_rehash_are_complete():
    original = runtime.terminal_output(claim())
    cases = list(runtime.invalid_outputs(original))
    assert len(cases) == 22 and len({c[0] for c in cases}) == 22
    assert sum(c[0].startswith("missing-") for c in cases) == 6
    assert sum(c[0].startswith("null-") for c in cases) == 5
    assert sum(c[0].startswith("wrong-type-") for c in cases) == 8
    for name, bad, state, message in cases:
        if name in ("source-name", "duplicate-kind", "missing-revision"):
            assert bad["artifact_manifest_sha256"] == runtime.digest(bad["artifact_manifest"])
            assert bad["terminal_receipt"]["artifact_manifest_sha256"] == bad["artifact_manifest_sha256"]
        assert state in ("P0001", "23514")
    assert original == runtime.terminal_output(claim())


def login():
    name, capability = runtime.ACTORS["worker"]
    return dict(session_user=name, current_user=name, database=runtime.DATABASE, version=180006,
                rolsuper=False, rolcreatedb=False, rolcreaterole=False, rolreplication=False,
                rolbypassrls=False, capabilities=[capability], definer_member=False,
                definer_set=False, table_access=0)


@pytest.mark.parametrize("key,value", [
    ("session_user", runtime.OWNER), ("current_user", "video_subset_owner"), ("version", 160015),
    ("database", "production"), ("rolsuper", True), ("rolcreatedb", True), ("rolcreaterole", True),
    ("rolreplication", True), ("rolbypassrls", True), ("capabilities", []),
    ("capabilities", ["video_subset_worker_v2", "video_subset_admitter"]),
    ("definer_member", True), ("definer_set", True), ("table_access", 1),
])
def test_runtime_login_authority_drift_rejected(key, value):
    state = login()
    state[key] = value
    with pytest.raises(AssertionError):
        runtime.check_login(state, *runtime.ACTORS["worker"])


def test_synthetic_subjects_receive_exactly_one_capability():
    sql = runtime.actor_setup_sql()
    assert sql.count("CREATE ROLE ") == sql.count("GRANT ") == 4
    assert "video_subset_owner" not in sql and "ON TABLE" not in sql
    assert sql.count("WITH ADMIN FALSE, INHERIT TRUE, SET FALSE") == 4
    assert sql.count("NOCREATEROLE NOREPLICATION NOBYPASSRLS") == 4
    assert runtime.actor_cleanup_sql().count("DROP ROLE ") == 4
    runtime.check_login(login(), *runtime.ACTORS["worker"])


@pytest.mark.parametrize("error", [None, psycopg.OperationalError("connection lost"),
    psycopg.errors.InsufficientPrivilege("permission denied"), psycopg.errors.CheckViolation("wrong constraint")])
def test_expected_failure_cannot_pass_on_success_or_wrong_error(error):
    conn = Mock()
    conn.transaction.side_effect = nullcontext
    fixture = runtime.Fixture({"worker": conn}, {"cases": []})
    call = Mock(side_effect=error)
    with pytest.raises(AssertionError):
        fixture.reject("test", "worker", call, "P0001", "VIDEO_QUEUE_RESULT_CONTRACT_INVALID")
    assert fixture.report["cases"] == []


def test_sql_routes_fail_closed_preserve_legacy_and_shared_tail():
    source042 = (ROOT / "database/tests/042_issue_881_canary_gate.sql").read_text(encoding="utf-8")
    legacy042 = source042.split("\\else\n", 1)[1].removesuffix("\n\\endif\n")
    assert install.sha(legacy042.encode()) == "84c992eeecb2d468fec5e7dca089fd2c465487cce6ff258e43dc2e75a52fd570"
    source043 = (ROOT / "database/tests/043_universal_video_terminal_size_guard.sql").read_text(encoding="utf-8")
    legacy043 = source043.split("\\else\n", 1)[1].split("\\endif\n", 1)[0]
    assert install.sha(legacy043.encode()) == "dc14ddce982f8050ce2ae782d3636803e97312cfc75a4bfdc280f8c54e9ddbd7"
    assert install.sha(source043.split("-- Exercise rollback/reapply", 1)[1].encode()) == "bbc4938d32c1cf1f8b122030dfed7a33c80b72d193ce359f2ff8a5cd91e7e7bf"
    assert source043.index("fixed-subset queue capabilities mismatch") < source043.index("\\if :fixed_subset_installed")
    for source in (source042, source043):
        assert "\\if :SHELL_ERROR\nSELECT 1/0;" in source
        assert "--execute" in source
        assert "GRANT " not in source and "REVOKE " not in source


def test_full_suite_runs_every_file_even_when_known_042_fails(monkeypatch, tmp_path):
    runner = install.Runner(ROOT, tmp_path)
    calls = []
    def command(label, argv, **kwargs):
        calls.append((label, argv, kwargs))
        return SimpleNamespace(returncode=3 if label.startswith("full_sql_042_") else 0, stdout="", stderr="")
    monkeypatch.setattr(runner, "command", command)
    report = {}
    with pytest.raises(install.RegressionFailure, match="042_issue_881_canary_gate.sql"):
        runner.full_preflight(report)
    assert len(report["sql_inventory"]) == len(report["sql_results"]) == 116
    assert [r["name"] for r in report["sql_results"]] == sorted(report["sql_inventory"])
    assert sum(r["status"] == "FAIL" for r in report["sql_results"]) == 1
    assert report["synthetic_subject_cleanup"] == "PASS"
    assert calls[-1][0] == "drop_synthetic_runtime_subjects"
    for label, argv, kwargs in calls:
        assert argv[1] == install.OWNER_DSN
        if label.startswith("full_sql_"):
            assert "-f" in argv and kwargs["extra_env"]["FIXED_SUBSET_DISPOSABLE_PREFLIGHT"] == "1"
