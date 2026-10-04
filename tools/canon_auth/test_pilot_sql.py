from uuid import UUID
import pytest
from .pilot_sql import plan, guard
from .test_auth import binding_fixture
from . import vercel_validator as v
from .verify_binding import verify_phase, verify_receipts


def test_plan_is_deterministic_and_contains_only_minimal_public_meanings():
    p = plan(UUID(int=101), "0" * 40)
    assert p == plan(UUID(int=101), "0" * 40)
    assert len(p["semantic_cases"]) == 18
    assert all(c["observed"] == c["expected"] for c in p["semantic_cases"])
    assert sum(s.startswith("INSERT INTO") for s in p["baseline"]) == 2
    assert sum(s.startswith("INSERT INTO") for s in p["initial"]) == 32
    assert sum(s.startswith("INSERT INTO") for s in p["revoke"]) == 1
    assert sum(s.startswith("INSERT INTO") for s in p["reactivate"]) == 5
    assert "IS NOT TRUE" in guard("NULL", "FIXED")
    assert not any("24 hours" in s for s in p["reactivate"])
    assert all("DELETE" not in s for k in ("baseline", "initial", "revoke", "reactivate", "emergency") for s in p[k])


def test_plan_input_validation_and_school_isolation():
    with pytest.raises(ValueError): plan("not-a-uuid", "0" * 40)
    with pytest.raises(ValueError): plan(UUID(int=101), "bad")
    p, q = plan(UUID(int=101), "0" * 40), plan(UUID(int=102), "0" * 40)
    assert p["ids"]["source"] != q["ids"]["source"]
    assert p["ids"]["position"] == q["ids"]["position"] == v.POSITION


def phase_fixture(tmp_path):
    result, before, after, logs = binding_fixture(tmp_path)
    receipt = result["receipts"][-1] | {"status_code": 200}
    log = logs[-1] | {"status_code": 200}
    phase = dict(schema=v.INTENT, status="pending_deployment_correlation", ready_sha=v.READY_SHA,
                 deployment_id=v.DEPLOYMENT, phase="baseline", receipts=[receipt],
                 answers=[{"call": "3H", "status": "ABSTAIN"}])
    return phase, before, after, [log]


def test_phase_requires_own_deployment_log_and_unused_receipt(tmp_path):
    args = phase_fixture(tmp_path)
    assert verify_phase(*args, previous_ids=set())["status"] == "phase_correlated"
    with pytest.raises(v.Rejected):
        verify_phase(*args, previous_ids={args[0]["receipts"][0]["request_id"]})
    args[-1][0]["deployment_id"] = "other"
    with pytest.raises(v.Rejected): verify_phase(*args, previous_ids=set())


@pytest.mark.parametrize("change", ["answer", "path", "count", "phase", "status", "duplicate"])
def test_phase_malformed_or_uncorrelated_is_not_accepted(tmp_path, change):
    result, before, after, logs = phase_fixture(tmp_path)
    if change == "answer": result["answers"][0]["status"] = "SUPPORTED"
    if change == "path": result["receipts"][0]["path"] = "/other"
    if change == "count": logs.clear()
    if change == "phase": result["phase"] = "active"
    if change == "status": result["status"] = "PASS"
    if change == "duplicate": result["receipts"] *= 2
    with pytest.raises(v.Rejected): verify_phase(result, before, after, logs, set())


def test_complete_poll_audit_requires_every_receipt(tmp_path):
    result, before, after, logs = binding_fixture(tmp_path)
    assert verify_receipts(result["receipts"], before, after, logs)["count"] == 3
    with pytest.raises(v.Rejected): verify_receipts(result["receipts"], before, after, logs[:-1])
