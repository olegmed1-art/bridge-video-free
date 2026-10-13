"""Offline control-flow/fixture checks. Every external process is replaced by a spy.

These tests cannot establish PostgreSQL privileges or transaction semantics.
The managed PG18 installation remains NOT_RUN until the isolated runner executes.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "database/tests/fixed_subset_admission/managed_owner_install.py"
SPEC = importlib.util.spec_from_file_location("managed_owner_install", SCRIPT)
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)
SOURCE = (ROOT / "database/migrations" / install.MIGRATION).read_bytes()


def result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def server(*, admin=False):
    return dict(version=180000, database=install.DATABASE,
        session_user="postgres" if admin else install.OWNER,
        current_user="postgres" if admin else install.OWNER,
        superuser=admin, createrole=True, createdb=admin, replication=admin, bypassrls=admin,
        self_grant="", max_connections=8, prepared=0, other_sessions=0,
        user_roles=[], user_schemas=[], public_relations=0, default_acls=0,
        database_owner=install.OWNER, public_owner=install.OWNER)


def pre():
    return dict(schema_owner=install.OWNER,
        legacy_tables={name: install.OWNER for name in ("batch", "job", "job_event")},
        legacy_functions={str(i): install.OWNER for i in range(5)},
        subset_roles=[], marker=False)


def post():
    names, grants = install.expected_api(SOURCE.decode())
    return dict(roles={name: [False] * 5 for name in install.ROLES},
        tables={name: install.ROLES[0] for name in install.TABLES},
        functions={name: install.ROLES[0] for name in names},
        create=False, set=False, usage=False,
        memberships=[[name, install.OWNER, "postgres", True, False, False] for name in install.ROLES],
        legacy_execute=[False, False], public_subset_execute=0,
        capability_execute=grants, direct_table_access=0,
        checksum=install.sha(SOURCE), new_rows=0)


@pytest.fixture(autouse=True)
def no_external_effects(monkeypatch):
    network = Mock(side_effect=AssertionError("network forbidden in offline test"))
    process = Mock(side_effect=AssertionError("real process forbidden in offline test"))
    monkeypatch.setattr(socket, "create_connection", network)
    monkeypatch.setattr(install.subprocess, "run", process)
    monkeypatch.setattr(install.subprocess, "Popen", process)
    return network, process


def test_default_command_and_import_do_not_execute_sql(capsys, no_external_effects):
    assert install.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "NOT_RUN" and report["sql_execution"] is False
    assert len(report["cases"]) == 7
    for spy in no_external_effects:
        spy.assert_not_called()


def test_original_fixture_is_exact_pre_fix_migration():
    cases = install.negative_cases(SOURCE.decode())
    assert install.sha(cases[0][1].encode()) == install.ORIGINAL_SHA256
    assert all(sql.count("BEGIN;") == 1 for _, sql, _, _ in cases)
    rollback = cases[-1][1]
    assert "COMMIT;" not in rollback and rollback.endswith("ROLLBACK;\n")
    assert "grantor='postgres'::regrole" in rollback
    assert "grantor=current_user::regrole" in rollback


def test_fixture_refuses_silent_rebase_or_changed_anchors():
    with pytest.raises(install.RegressionFailure, match="reviewed bytes"):
        install.negative_cases(SOURCE.decode().replace("SUBSET_REQUIRES_0059", "CHANGED"))
    with pytest.raises(install.RegressionFailure, match="anchor"):
        install.once("two two", "two", "one")


@pytest.mark.parametrize("key,value", [
    ("version", 160015), ("version", 190000), ("database", "production"),
    ("session_user", "postgres"), ("current_user", "video_subset_owner"),
    ("superuser", True), ("createrole", False), ("createdb", True),
    ("replication", True), ("bypassrls", True), ("self_grant", "inherit,set"),
    ("max_connections", 9), ("max_connections", 0), ("prepared", 1),
    ("other_sessions", 1), ("default_acls", 1), ("database_owner", "other"),
    ("public_owner", "other"),
])
def test_wrong_managed_preflight_is_rejected(key, value):
    state = server()
    state[key] = value
    with pytest.raises(install.RegressionFailure):
        install.check_server(state)


@pytest.mark.parametrize("key,value", [
    ("user_roles", ["existing_user"]), ("user_schemas", ["school"]),
    ("public_relations", 1), ("superuser", False),
])
def test_admin_bootstrap_requires_fresh_cluster(key, value):
    state = server(admin=True)
    state[key] = value
    with pytest.raises(install.RegressionFailure):
        install.check_server(state, admin=True)


@pytest.mark.parametrize("key,value", [
    ("schema_owner", "postgres"), ("legacy_tables", {}),
    ("legacy_functions", {"claim": "postgres"}), ("marker", True),
    ("subset_roles", ["video_subset_owner"]),
])
def test_wrong_original_ownership_is_rejected(key, value):
    state = pre()
    state[key] = value
    with pytest.raises(install.RegressionFailure):
        install.check_pre(state)


@pytest.mark.parametrize("key,value", [
    ("create", True), ("set", True), ("usage", True), ("memberships", []),
    ("roles", {}), ("tables", {}), ("functions", {}),
    ("legacy_execute", [True, False]), ("public_subset_execute", 1),
    ("capability_execute", []), ("direct_table_access", 1),
    ("checksum", "wrong"), ("new_rows", 1),
])
def test_post_commit_privilege_or_checksum_drift_is_rejected(key, value):
    state = post()
    state[key] = value
    with pytest.raises(install.RegressionFailure):
        install.check_post(state, install.sha(SOURCE), SOURCE.decode())


def test_only_m_issued_self_grant_is_removed():
    state = post()
    state["memberships"].append(
        ["video_subset_owner", install.OWNER, install.OWNER, False, True, True])
    with pytest.raises(install.RegressionFailure, match="self-grant"):
        install.check_post(state, install.sha(SOURCE), SOURCE.decode())
    state = post()
    state["memberships"][0][-1] = True  # bootstrap SET privilege must stay false
    with pytest.raises(install.RegressionFailure, match="bootstrap"):
        install.check_post(state, install.sha(SOURCE), SOURCE.decode())
    install.check_post(post(), install.sha(SOURCE), SOURCE.decode())


def test_retained_legacy_read_grant_is_required_but_legacy_write_is_rejected():
    origin = (ROOT / "database/migrations/0058_universal_video_terminal_v2_gate"
              "/001_apply_terminal_v2.sql").read_text()
    assert "GRANT EXECUTE ON FUNCTION video_queue.precanary_idle_snapshot() TO bridge_school_worker;" in origin
    state = post()
    state["capability_execute"].remove(install.LEGACY_READ_GRANT)
    with pytest.raises(install.RegressionFailure, match="matrix"):
        install.check_post(state, install.sha(SOURCE), SOURCE.decode())
    state = post()
    state["capability_execute"].append(["bridge_school_worker", "video_queue.claim_job(text,integer,text,text)"])
    with pytest.raises(install.RegressionFailure, match="matrix"):
        install.check_post(state, install.sha(SOURCE), SOURCE.decode())


@pytest.mark.parametrize("response", [
    result(), result(stderr="ERROR:  42601: syntax error", returncode=3),
    result(stderr="ERROR:  P0001: another assertion failed", returncode=3),
    result(stderr="connection refused", returncode=2),
])
def test_negative_case_requires_exact_error(response):
    with pytest.raises(install.RegressionFailure):
        install.expect_result(response, state="P0001", message="required queue capabilities are missing")


class SyntheticProcesses:
    """Responses model control flow only; they are not a SQL engine."""
    def __init__(self, *, fault=None):
        self.calls = []
        self.migrations = 0
        self.snapshots = 0
        self.fault = fault
        self.cases = {sql: (state, message) for _, sql, state, message
                      in install.negative_cases(SOURCE.decode())}

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, deepcopy(kwargs)))
        sql = kwargs["input"]
        if argv[0] == "bash":
            self.migrations += 1
            staged = Path(kwargs["env"]["MIGRATIONS_DIR"])
            if self.migrations == 1:
                assert staged != ROOT / "database/migrations"
                assert max(p.name[:4] for p in staged.glob("*.sql")) == "0059"
                parts = list(staged.glob("0058*/*.sql"))
                assert parts, "composite migration fixtures must not be dropped"
            else:
                assert staged == ROOT / "database/migrations"
            return result("Skipping already-applied migration " + install.KEY)
        admin = argv[1] == install.ADMIN_DSN
        if sql == install.SERVER_SQL:
            return result(json.dumps(server(admin=admin)))
        if sql == install.BOOTSTRAP_SQL:
            assert admin
            return result()
        if sql == install.OWNERS_SQL:
            return result(json.dumps(pre()))
        if sql == install.POST_SQL:
            state = post()
            if self.fault == "leaked_create":
                state["create"] = True
            return result(json.dumps(state))
        if sql == install.SNAPSHOT_SQL:
            self.snapshots += 1
            phase = "committed" if self.migrations >= 2 else "baseline"
            if self.fault == "rollback_drift" and self.snapshots == 2:
                phase = "wrong_acl"
            if self.fault == "rerun_drift" and self.migrations == 3:
                phase = "wrong_acl"
            return result(json.dumps({"catalog_and_rows": phase}))
        if sql in self.cases:
            assert not admin
            state, message = self.cases[sql]
            if self.fault == "unexpected_negative_success":
                return result()
            return result() if state is None else result(
                stderr=f"ERROR:  {state}: {message or 'permission denied'}", returncode=3)
        assert "SUBSET_RUNTIME_EXECUTE_MATRIX" in sql and not admin
        if "-- MANAGED ACL MUTATION ANCHOR" not in sql:
            return result(stderr="ERROR:  P0001: SUBSET_RUNTIME_EXECUTE_MATRIX", returncode=3)
        if self.fault == "wrong_041_failure":
            return result(stderr="ERROR:  P0001: unrelated failure", returncode=3)
        return result()


def popen_adapter(processes):
    class Process:
        def __init__(self, argv, **kwargs):
            assert kwargs.pop("start_new_session") is True
            for key in ("stdin", "stdout", "stderr"):
                assert kwargs.pop(key) == install.subprocess.PIPE
            self.argv, self.kwargs = argv, kwargs
            self.returncode = None

        def communicate(self, input=None, *, timeout):
            response = processes(self.argv, **self.kwargs, input=input, timeout=timeout)
            self.returncode = response.returncode
            return response.stdout, response.stderr
    return Mock(side_effect=Process)


def test_whole_future_scenario_uses_real_migrator_as_M_and_no_superuser_migrations(monkeypatch, tmp_path):
    processes = SyntheticProcesses()
    monkeypatch.setenv("DATABASE_URL", "must-not-inherit")
    monkeypatch.setenv("PGSERVICE", "must-not-inherit")
    monkeypatch.setattr(install.subprocess, "Popen", popen_adapter(processes))
    report = install.Runner(ROOT, tmp_path / "evidence").execute()
    assert report["status"] == "PASS_OWNER_INSTALL_ONLY"
    assert report["production_preflight"] == "NOT_RUN_FULL_SUITE"
    assert report["invariant_041"] == "PASS_BEFORE_AND_AFTER_CUTOVER"
    assert len(report["acl_negative_cases"]) == 2
    assert len(report["negative_cases"]) == 7 and processes.migrations == 3
    for argv, kwargs in processes.calls:
        assert not kwargs.get("shell") and kwargs["timeout"] <= 600
        assert "PGSERVICE" not in kwargs["env"]
        if argv[0] == "bash":
            assert kwargs["env"]["DATABASE_URL"] == install.OWNER_DSN
            assert argv[1] == str(ROOT / "database/scripts/migrate.sh")
        else:
            assert "DATABASE_URL" not in kwargs["env"]
            if argv[1] == install.ADMIN_DSN:
                assert kwargs["input"] == install.BOOTSTRAP_SQL or kwargs["input"].lstrip().startswith("SELECT")
    saved = json.loads((tmp_path / "evidence/results.json").read_text())
    assert saved["migration_sha256"] == install.sha(SOURCE)
    assert install.MIGRATION in saved["migration_inventory"]


@pytest.mark.parametrize("fault", [
    "rollback_drift", "rerun_drift", "leaked_create",
    "unexpected_negative_success", "wrong_041_failure",
])
def test_future_scenario_fails_closed_and_saves_evidence(monkeypatch, tmp_path, fault):
    monkeypatch.setattr(install.subprocess, "Popen", popen_adapter(SyntheticProcesses(fault=fault)))
    with pytest.raises(install.RegressionFailure):
        install.Runner(ROOT, tmp_path / "evidence").execute()
    saved = json.loads((tmp_path / "evidence/results.json").read_text())
    assert saved["status"] == "FAIL_OR_UNKNOWN"
    assert saved["events"]


def test_timeout_kills_group_not_only_bash_and_saves_timeout(monkeypatch, tmp_path):
    process = Mock(pid=54321, returncode=None)
    process.communicate.side_effect = [
        install.subprocess.TimeoutExpired("bash", 1),
        install.subprocess.TimeoutExpired("bash", 5), ("partial stdout", "partial stderr")]
    monkeypatch.setattr(install.subprocess, "Popen", Mock(return_value=process))
    kill = Mock()
    monkeypatch.setattr(install.os, "killpg", kill, raising=False)
    runner = install.Runner(ROOT, tmp_path)
    with pytest.raises(install.RegressionFailure, match="timeout"):
        runner.command("migration", ["bash", "migrate.sh"], timeout=1)
    assert [call.args for call in kill.call_args_list] == [
        (54321, install.signal.SIGTERM), (54321, 9)]
    assert runner.events[0]["timeout"] is True
    assert runner.events[0]["database_cleanup"].startswith("NOT_PROVEN")
    assert runner.events[0]["stderr"] == "partial stderr"


def test_report_write_failure_does_not_hide_primary_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(install.subprocess, "Popen",
                        popen_adapter(SyntheticProcesses(fault="unexpected_negative_success")))
    monkeypatch.setattr(Path, "write_text", Mock(side_effect=OSError("synthetic disk full")))
    with pytest.raises(install.RegressionFailure, match="expected SQLSTATE") as error:
        install.Runner(ROOT, tmp_path / "evidence").execute()
    assert any("synthetic disk full" in note for note in error.value.__notes__)


def test_interrupt_cleans_group_and_secondary_cleanup_error_cannot_hide_it(monkeypatch, tmp_path):
    primary = KeyboardInterrupt("synthetic interruption")
    process = Mock(pid=54321, returncode=None)
    process.communicate.side_effect = [primary, OSError("synthetic wait error"), ("", "")]
    monkeypatch.setattr(install.subprocess, "Popen", Mock(return_value=process))
    kill = Mock(side_effect=[OSError("synthetic signal error"), None])
    monkeypatch.setattr(install.os, "killpg", kill, raising=False)
    runner = install.Runner(ROOT, tmp_path)
    with pytest.raises(KeyboardInterrupt) as error:
        runner.command("migration", ["bash", "migrate.sh"])
    assert error.value is primary
    assert kill.call_count == 2
    assert runner.events[0]["error"] == "KeyboardInterrupt"
    assert runner.events[0]["timeout"] is False
    assert len(runner.events[0]["cleanup"]) == 3


def test_041_retains_old_scenario_and_matches_exact_target_acl():
    source = (ROOT / "database/tests/041_universal_video_queue.sql").read_text()
    managed, legacy = source.split("\\else\n", 1)
    legacy = legacy.removesuffix("\\endif\n")
    assert install.sha(legacy.encode()) == "68fa8b4d5eabe5e76a0f8d81dfbd156d7bf0bf5eaa7456891621f2a7e02f0b50"
    assert "migration_key='0060_fixed_subset_admission'" in managed
    actual = sorted([role, signature] for role, signature in install.re.findall(
        r"\('((?:video_subset_|bridge_school_)\w+)','(video_queue\.[^']+)'\)", managed))
    assert actual == install.expected_api(SOURCE.decode())[1]
    assert "GRANT " not in managed and "REVOKE " not in managed
    assert managed.index("BEGIN;") < managed.index("-- MANAGED ACL MUTATION ANCHOR")
    assert managed.rstrip().endswith("ROLLBACK;")


def test_one_shot_ci_has_no_deploy_secrets_or_paid_storage():
    import ast
    import yaml
    text = (ROOT / ".github/workflows/managed-owner-postgres.yml").read_text()
    workflow = yaml.safe_load(text)
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {"push": {"branches": ["codex-managed-owner-pg18-ci3"]}}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"managed-owner"}
    job = workflow["jobs"]["managed-owner"]
    assert job["runs-on"] == "ubuntu-24.04" and job["timeout-minutes"] == 20
    assert "github.run_attempt == 1" in job["if"] and "github.event.created == true" in job["if"]
    assert "environment" not in job
    service = job["services"]["postgres"]
    assert service["ports"] == ["127.0.0.1:5432:5432"]
    assert service["image"].endswith("@sha256:afc7e2d441324c0388fa80c3d24f733b4194a4eb7f47dd8ee2b08eb1a24a647c")
    assert "max_connections=8" in service["env"]["POSTGRES_INITDB_ARGS"]
    assert "secrets." not in text and "upload-artifact" not in text and "actions/cache" not in text
    actions = [s for s in job["steps"] if "uses" in s]
    assert len(actions) == 2 and actions[0]["with"]["persist-credentials"] is False
    for step in job["steps"]:
        script = step.get("run", "")
        if "python - <<'PY'\n" in script:
            source = script.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
            ast.parse(source)
