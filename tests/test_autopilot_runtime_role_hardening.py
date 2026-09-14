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
    assert source.count("BEGIN;") >= 2 and source.count("COMMIT;") >= 2
    assert "ON_ERROR_STOP=1" in source


def test_hardening_provisions_isolated_login_and_rotates_credential_safely() -> None:
    source = WORKFLOW.read_text()
    assert "autopilot_runtime_worker_login" in source
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in source
    assert "autopilot_runtime_principal" in source
    assert "AUTOPILOT_SUCCESSOR_LOGIN_LEAST_PRIVILEGE_FAILED" in source
    assert "has_table_privilege" in source
    assert "has_function_privilege" in source
    assert "autopilot.claim_project_work_probe(text,integer)" in source
    assert "autopilot.materialize_project_work_probe(uuid,text,bigint,boolean,text)" in source
    assert "autopilot.fail_project_work_probe(uuid,text,bigint,text,boolean)" in source
    assert "autopilot.project_work_item" in source
    assert "openssl rand -base64 36" in source
    assert "::add-mask::$runtime_password" in source
    assert "ALTER ROLE autopilot_runtime_login PASSWORD" not in source
    assert "REVOKE neon_superuser FROM autopilot_runtime_login" not in source
    assert "AUTOPILOT_CALLBACK_DATABASE_URL" not in source
    assert "Credentials were rotated without being printed" in source


def test_hardening_uses_pinned_encrypted_cutover_and_rollback() -> None:
    source = WORKFLOW.read_text()
    assert "EXPECTED_FINGERPRINT: SHA256:" in source
    assert "sudo -n cat /etc/ssh/ssh_host_rsa_key.pub" in source
    assert "StrictHostKeyChecking=yes" in source
    assert "rsa_padding_mode:oaep" in source
    assert "rsa_oaep_md:sha256" in source
    assert "/etc/ssh/ssh_host_rsa_key" in source
    assert "ssh-keygen -p -m PEM -N '' -P ''" in source
    assert "AUTOPILOT_EXPECTED_DB_USER" in source
    assert "os.replace(temporary, path)" in source
    assert "AUTOPILOT_RUNTIME_LOGIN_CUTOVER_ROLLBACK=PASS" in source
    assert "systemctl restart \"$service\"" in source
    assert "AUTOPILOT_SUCCESSOR_LOGIN_LIVE=PASS" in source
    assert "print(values" not in source
    assert "echo $AUTOPILOT" not in source
