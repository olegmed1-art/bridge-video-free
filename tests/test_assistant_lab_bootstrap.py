from pathlib import Path

import pytest

from assistant_lab.bootstrap_contract import BootstrapContractError, build_bootstrap_script, token_digest


PAYLOAD = {
    "database_url": "postgresql://assistant_lab_worker_principal:secret@ep-example-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require",
    "expected_db_user": "assistant_lab_worker_principal",
    "public_ip": "158.180.47.161",
    "code_sha": "a" * 40,
    "repo_url": "https://github.com/olegmed1-art/bridge-video-free.git",
}


def test_bootstrap_token_is_digest_only_and_bounded():
    token = "x" * 64
    digest = token_digest(token)
    assert len(digest) == 64
    assert token not in digest
    with pytest.raises(BootstrapContractError):
        token_digest("short")


def test_bootstrap_script_is_pinned_host_local_and_secret_quiet():
    script = build_bootstrap_script(PAYLOAD)
    assert "git checkout --quiet --detach" in script
    assert "oracle_dds3_host_repair.sh" in script
    assert "oracle_assistant_lab_install.sh" in script
    assert "ASSISTANT_LAB_ACTIVATE=1" in script
    assert "set +x" in script
    assert "echo $ASSISTANT_LAB_DATABASE_URL" not in script
    assert "ASSISTANT_LAB_OCI_BOOTSTRAP_PASS" in script


def test_bootstrap_contract_rejects_wrong_target_and_repo():
    with pytest.raises(BootstrapContractError):
        build_bootstrap_script({**PAYLOAD, "public_ip": "not-an-ip"})
    with pytest.raises(BootstrapContractError):
        build_bootstrap_script({**PAYLOAD, "expected_db_user": "bridge_school_worker_principal"})
    with pytest.raises(BootstrapContractError):
        build_bootstrap_script({**PAYLOAD, "repo_url": "https://example.com/repo.git"})


def test_bootstrap_schema_is_owner_only_and_security_definer():
    schema = Path("assistant_lab/bootstrap_schema.sql").read_text(encoding="utf-8").lower()
    assert "security definer" in schema
    assert "set search_path = pg_catalog, assistant_lab" in schema
    assert "revoke all on assistant_lab.bootstrap_ticket from public, bridge_school_app, assistant_lab_worker" in schema
    assert "grant execute on function assistant_lab.claim_bootstrap_ticket(text) to bridge_school_app" in schema
    assert "claim_count < 3" in schema


def test_vercel_entrypoint_registers_bootstrap_without_general_api_dependency():
    app = Path("app.py").read_text(encoding="utf-8")
    assert "assistant_lab_bootstrap_router" in app
    assert "app.include_router(assistant_lab_bootstrap_router)" in app
    assert "app.include_router(assistant_lab_bootstrap_router, dependencies=" not in app


def test_bootstrap_redemption_persists_capability_claim():
    source = Path("bridge_school_api/assistant_lab_bootstrap.py").read_text(encoding="utf-8")
    claim = 'cur.execute("SELECT assistant_lab.claim_bootstrap_ticket(%s) AS payload", (digest,))'
    claim_index = source.index(claim)
    fetch_index = source.index("row = cur.fetchone()", claim_index)
    commit_index = source.index("conn.commit()", fetch_index)
    response_index = source.index("return PlainTextResponse(", commit_index)
    assert claim_index < fetch_index < commit_index < response_index


def test_host_local_repair_does_not_require_oci_cli_or_ssh():
    repair = Path("ops/oracle_dds3_host_repair.sh").read_text(encoding="utf-8")
    assert "oci " not in repair
    assert "ssh " not in repair
    assert "127.0.0.1:8080/readyz" in repair
    assert "bridge-school-dds3-runtime" in repair
    assert "OCI_DDS3_HOST_REPAIR_PASS" in repair
