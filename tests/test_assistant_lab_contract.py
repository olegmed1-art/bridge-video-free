from pathlib import Path

import pytest

from assistant_lab.contract import (
    CONTRACT_VERSION,
    LabContractError,
    Priority,
    canonical_idempotency_key,
    validate_job_payload,
    validate_priority,
    verify_dds3_result,
    LabJob,
)
from assistant_lab.dispatch import dispatch_nonce_sha256, verify_dispatch_nonce
from assistant_lab.worker import WorkerConfig, execute_job, validate_local_dds3_url, validate_neon_dsn


def test_priority_contract_is_stable():
    assert int(Priority.INTERACTIVE) == 0
    assert int(Priority.REGRESSION) == 10
    assert int(Priority.EXPERIMENT) == 20
    assert int(Priority.BACKGROUND) == 30
    assert validate_priority(0) == 0
    with pytest.raises(LabContractError):
        validate_priority(5)


def test_dd_table_requires_bounded_pbn():
    payload = validate_job_payload("DDS3_COMPUTE", {"operation": "dd_table", "pbn": "N:AKQ..."})
    assert payload["operation"] == "dd_table"
    with pytest.raises(LabContractError):
        validate_job_payload("DDS3_COMPUTE", {"operation": "dd_table"})


def test_position_trajectory_is_bounded():
    base = {
        "operation": "position_trajectory",
        "positions": [{"pbn": "N:- - - -"}],
        "perspective": "ns",
    }
    assert validate_job_payload("DDS3_COMPUTE", base)["perspective"] == "NS"
    too_many = dict(base, positions=[{}] * 61)
    with pytest.raises(LabContractError):
        validate_job_payload("DDS3_COMPUTE", too_many)


def test_arbitrary_job_and_dds3_operations_are_rejected():
    with pytest.raises(LabContractError):
        validate_job_payload("SHELL", {"command": "id"})
    with pytest.raises(LabContractError):
        validate_job_payload("DDS3_COMPUTE", {"operation": "image_dd_table"})


def test_world_generation_job_is_bounded_and_explicit():
    payload = {
        "known_seat": "n",
        "known_hand_pbn": "AKQJ.T98.765.432",
        "constraints": {"seats": {"E": {"hcp": [10, 12]}}},
        "count": 32,
        "seed": 17,
    }
    validated = validate_job_payload("WORLD_GENERATE", payload)
    assert validated["known_seat"] == "N"
    assert validated["count"] == 32
    with pytest.raises(LabContractError):
        validate_job_payload("WORLD_GENERATE", dict(payload, count=1001))
    with pytest.raises(LabContractError):
        validate_job_payload("WORLD_GENERATE", dict(payload, seed=None))


def test_idempotency_key_is_deterministic_and_versioned():
    a = canonical_idempotency_key("DDS3_COMPUTE", {"pbn": "N:AKQ...", "operation": "dd_table"})
    b = canonical_idempotency_key("dds3_compute", {"operation": "dd_table", "pbn": "N:AKQ..."})
    assert a == b
    assert a.startswith(CONTRACT_VERSION + ":")


def test_dds3_provenance_is_fail_closed():
    good = {"engine": "DDS3", "fallback_used": False, "operation": "dd_table"}
    assert verify_dds3_result(good, expected_operation="dd_table") == good
    with pytest.raises(LabContractError):
        verify_dds3_result({"engine": "DDS", "fallback_used": False, "operation": "dd_table"})
    with pytest.raises(LabContractError):
        verify_dds3_result({"engine": "DDS3", "fallback_used": True, "operation": "dd_table"})
    with pytest.raises(LabContractError):
        verify_dds3_result(good, expected_operation="position_all_moves")


def test_dispatch_capability_is_hashed_and_fail_closed():
    nonce = "ab" * 32
    digest = dispatch_nonce_sha256(nonce)
    assert len(digest) == 64
    assert verify_dispatch_nonce(digest, nonce) is True
    assert verify_dispatch_nonce(digest, "cd" * 32) is False
    assert verify_dispatch_nonce(None, nonce) is False
    with pytest.raises(LabContractError):
        dispatch_nonce_sha256("short")


def test_worker_dsn_is_neon_tls_channel_bound(monkeypatch):
    monkeypatch.setenv("ASSISTANT_LAB_EXPECTED_DB_USER", "assistant_lab_worker_principal")
    good = (
        "postgresql://assistant_lab_worker_principal:secret@"
        "ep-example-pooler.eu-central-1.aws.neon.tech/neondb"
        "?sslmode=require&channel_binding=require"
    )
    assert validate_neon_dsn(good) == good
    with pytest.raises(RuntimeError):
        validate_neon_dsn(good.replace("channel_binding=require", "channel_binding=disable"))
    with pytest.raises(RuntimeError):
        validate_neon_dsn(good.replace("assistant_lab_worker_principal", "bridge_school_worker_principal"))
    with pytest.raises(RuntimeError):
        validate_neon_dsn(good.replace("neon.tech", "example.com"))


def test_worker_dds3_endpoint_is_localhost_only():
    good = "http://127.0.0.1:8080/v1/compute"
    assert validate_local_dds3_url(good) == good
    assert validate_local_dds3_url("http://localhost:8080/v1/compute") == "http://localhost:8080/v1/compute"
    with pytest.raises(RuntimeError):
        validate_local_dds3_url("https://example.com/v1/compute")
    with pytest.raises(RuntimeError):
        validate_local_dds3_url("http://127.0.0.1:8081/v1/compute")
    with pytest.raises(RuntimeError):
        validate_local_dds3_url("http://127.0.0.1:8080/anything")


def test_worker_executes_world_generation_without_network():
    job = LabJob(
        job_id="00000000-0000-0000-0000-000000000001",
        kind="WORLD_GENERATE",
        payload=validate_job_payload("WORLD_GENERATE", {
            "known_seat": "N", "known_hand_pbn": "AKQJ.T98.765.432",
            "constraints": {}, "count": 2, "seed": 3,
        }),
        priority=20, attempts=1, max_attempts=2,
    )
    config = WorkerConfig("unused", "test", "http://127.0.0.1:8080/v1/compute", "token")
    result = execute_job(job, config)
    assert result["engine"] == "WORLD_GENERATOR"
    assert result["accepted"] == 2


def test_worker_rejects_unknown_kind_inside_execution_boundary():
    job = LabJob(
        job_id="00000000-0000-0000-0000-000000000002",
        kind="FUTURE_UNKNOWN_KIND", payload={}, priority=20, attempts=1, max_attempts=2,
    )
    config = WorkerConfig("unused", "test", "http://127.0.0.1:8080/v1/compute", "token")
    with pytest.raises(LabContractError):
        execute_job(job, config)


def test_schema_is_isolated_and_dispatch_is_update_only_for_app():
    schema = Path("assistant_lab/schema.sql").read_text(encoding="utf-8")
    lowered = schema.lower()
    assert "create schema if not exists assistant_lab" in lowered
    assert "dispatch_nonce_sha256" in lowered
    assert "normalize_dispatch_provenance" in lowered
    assert "vercel_oidc_to_oracle_dds3" in lowered
    assert "world_generate" in lowered
    assert "revoke all on all tables in schema assistant_lab from public" in lowered
    assert "grant select, update on assistant_lab.job to bridge_school_app" in lowered
    assert "grant insert" not in lowered
    assert "create role" not in lowered
    assert "alter role" not in lowered
    assert "drop schema" not in lowered
    assert "public.skill" not in lowered
    assert "ai.system_rule" not in lowered


def test_capability_route_cannot_accept_payload_in_url():
    main = Path("bridge_school_api/main.py").read_text(encoding="utf-8")
    assert '/v1/assistant-lab/jobs/{job_id}/dispatch' in main
    assert "verify_dispatch_nonce" in main
    assert "validate_job_payload(row[\"kind\"], row[\"payload_json\"])" in main
    assert "payload:" not in main.split('def dispatch_assistant_lab_job', 1)[1].split('def _configuration_failure_category', 1)[0]


def test_vercel_runtime_package_includes_assistant_lab():
    rules = Path(".vercelignore").read_text(encoding="utf-8").splitlines()
    assert "!assistant_lab" in rules
    assert "!bridge_school_api" in rules


def test_oracle_service_uses_dedicated_unix_identity_and_hardening():
    unit = Path("deploy/oracle-assistant-lab/assistant-lab.service").read_text(encoding="utf-8")
    assert "User=assistant-lab" in unit
    assert "Group=assistant-lab" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "PrivateDevices=true" in unit
    assert "EnvironmentFile=/opt/bridge-school/assistant-lab/assistant-lab.env" in unit
    assert "ExecStart=" in unit and "-m assistant_lab.worker" in unit


def test_oracle_installer_is_fail_closed_secret_safe_and_stage_idempotent():
    installer = Path("ops/oracle_assistant_lab_install.sh").read_text(encoding="utf-8")
    assert "ASSISTANT_LAB_DATABASE_URL is required" in installer
    assert "http://127.0.0.1:8080/readyz" in installer
    assert "assistant_lab_worker_principal" in installer
    assert "ASSISTANT_LAB_INSTALL_PASS" in installer
    assert "ASSISTANT_LAB_ACTIVATE" in installer
    assert "ASSISTANT_LAB_PSYCOPG_VERSION:-3.2.13" in installer
    assert "state_unchanged=1" in installer
    assert "systemctl disable --now" not in installer
    assert "echo $ASSISTANT_LAB_DATABASE_URL" not in installer
    assert "set -x" not in installer
