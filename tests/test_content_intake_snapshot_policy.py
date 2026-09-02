import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/research/bidding-engine/content-intake/content-intake-snapshot.json"


def _snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _lane(data: dict, lane_id: str) -> dict:
    return next(lane for lane in data["lanes"] if lane["lane_id"] == lane_id)


def test_mutable_catalog_identity_fails_closed() -> None:
    data = _snapshot()
    identity = data["catalog_snapshot_identity"]
    assert identity["verification_status"] == "UNVERIFIED_IMMUTABLE_EXPORT_UNAVAILABLE"
    assert identity["immutable_export_revision"] is None
    assert identity["immutable_export_sha256"] is None
    assert identity["counts_authoritative"] is False
    assert identity["staging_or_activation_authorized"] is False

    for lane_id in identity["dependent_lanes"]:
        lane = _lane(data, lane_id)
        assert lane["catalog_counts_verification"] == "UNVERIFIED_IMMUTABLE_EXPORT_UNAVAILABLE"
        assert lane["metadata_staging_allowed"] is False


def test_rejected_course_notes_require_exact_canon_owner_reversal() -> None:
    data = _snapshot()
    notes = _lane(data, "LEARNING_CONTENT")["course_notes"]
    decision = notes["quarantine_decision"]
    reversal = decision["reversal_required"]

    assert notes["canon_source_eligibility"] == "PERSISTENTLY_QUARANTINED"
    assert decision["applies_to"] == "ALL_FUTURE_SCHOOL_CANON_INGESTION"
    assert decision["fail_closed_if_reversal_missing_or_unbound"] is True
    assert reversal == {
        "approver_role": "canon_owner",
        "explicit_decision_record": True,
        "exact_source_version_binding": True,
        "semantic_scope_binding": True,
    }


def test_world_promotion_and_canon_conflict_fail_closed() -> None:
    data = _snapshot()
    promotion = _lane(data, "WORLD_EXTERNAL")["canon_promotion"]
    requirements = set(promotion["requirements"])

    assert promotion["status"] == "FORBIDDEN_BY_DEFAULT"
    assert promotion["required_approver_role"] == "canon_owner"
    assert promotion["non_owner_approval_accepted"] is False
    assert promotion["silent_promotion_allowed"] is False
    assert promotion["on_missing_or_stale_evidence"] == "FAIL_CLOSED_NO_PROMOTION"
    assert promotion["on_canon_conflict"] == "RETURN_CANON_CONFLICT_NO_ACTION_WORLD_NOT_CALLED"
    assert {
        "exact_world_source_version_and_sha256",
        "exact_target_canon_version",
        "exact_semantic_diff_and_scope",
        "provenance_chain",
        "regression_and_integrity_tests",
        "minimum_independent_assurance_I2",
    } <= requirements


def test_no_batch_from_unverified_catalog_can_activate() -> None:
    data = _snapshot()
    batches = {batch["batch_id"]: batch for batch in data["intake_batches"]}
    for batch_id in ("L1-001", "LEARN-001", "WORLD-001"):
        batch = batches[batch_id]
        assert batch["readiness"] == "blocked_until_catalog_snapshot_immutable_identity_verified"
        assert batch["metadata_staging_allowed"] is False
        assert batch["activation_allowed"] is False
