from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/autopilot-runtime-role-hardening.yml"


def test_hardening_is_exact_owner_bounded_and_atomic() -> None:
    source = WORKFLOW.read_text()
    document = yaml.safe_load(source)
    assert "github.event.comment.user.login == 'olegmed1-art'" in source
    assert "github.event.issue.number == 1131" in source
    assert "'/autopilot harden-runtime-role-v1'" in source
    assert "oracle-instance-workload-mutation" in source
    assert document["concurrency"]["cancel-in-progress"] is False
    assert "environment: database-production" in source
    assert "BEGIN;" in source and "COMMIT;" in source
    assert "ON_ERROR_STOP=1" in source


def test_hardening_removes_privilege_without_touching_credentials() -> None:
    source = WORKFLOW.read_text()
    assert "REVOKE neon_superuser FROM autopilot_runtime_login" in source
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in source
    assert "autopilot_runtime_principal" in source
    assert "AUTOPILOT_RUNTIME_LOGIN_LEAST_PRIVILEGE_FAILED" in source
    assert "has_table_privilege" in source
    assert "has_function_privilege" in source
    assert "autopilot.claim_project_work_probe(text,integer)" in source
    assert "autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)" in source
    assert "autopilot.fail_project_work_probe(uuid,text,bigint,text,boolean)" in source
    assert "autopilot.project_work_item" in source
    assert "ALTER ROLE autopilot_runtime_login PASSWORD" not in source
    assert "AUTOPILOT_CALLBACK_DATABASE_URL" not in source
    assert "No credential value was read or printed" in source
