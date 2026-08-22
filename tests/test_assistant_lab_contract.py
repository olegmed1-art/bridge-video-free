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


def test_schema_is_isolated_and_not_role_provisioning():
    schema = Path("assistant_lab/schema.sql").read_text(encoding="utf-8")
    lowered = schema.lower()
    assert "create schema if not exists assistant_lab" in lowered
    assert "create role" not in lowered
    assert "alter role" not in lowered
    assert "drop schema" not in lowered
    assert "public.skill" not in lowered
    assert "ai.system_rule" not in lowered
