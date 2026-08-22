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
)
from assistant_lab.dispatch import dispatch_nonce_sha256, verify_dispatch_nonce
from assistant_lab.worker import validate_local_dds3_url, validate_neon_dsn


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


def test_schema_is_isolated_and_dispatch_is_update_only_for_app():
    schema = Path("assistant_lab/schema.sql").read_text(encoding="utf-8")
    lowered = schema.lower()
    assert "create schema if not exists assistant_lab" in lowered
    assert "dispatch_nonce_sha256" in lowered
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


def test_oracle_service_is_fail_closed_and_not_public_network_worker():
    unit = Path("deploy/oracle-assistant-lab/assistant-lab.service").read_text(encoding="utf-8")
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "EnvironmentFile=/opt/bridge-school/assistant-lab/assistant-lab.env" in unit
    assert "ExecStart=" in unit and "-m assistant_lab.worker" in unit
