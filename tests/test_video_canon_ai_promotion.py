from __future__ import annotations

from copy import deepcopy
import hashlib
import json

import pytest

from bridge_contracts.video_canon_ai_promotion import (
    REQUIRED_CHECKS,
    STATE_DEPENDENT_CHECKS,
    VideoCanonAIPromotionError,
    build_ai_canon_promotion,
)
from bridge_contracts.video_canon_evidence import build_video_canon_candidate
from tests.test_video_canon_evidence import _assertion, _learning


def _candidate() -> dict:
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.98
    return build_video_canon_candidate(_learning(), assertion)


def _bundle(candidate: dict) -> dict:
    checks = []
    canon_snapshot_sha = "c" * 64
    for check_id in sorted(REQUIRED_CHECKS):
        family = "formal-checker"
        principal = "svc-formal-checker"
        assurance = "I1"
        if check_id == "SEMANTIC_PARSE":
            family, principal, assurance = "semantic-model-a", "svc-semantic-a", "I2"
        elif check_id == "BRIDGE_LOGIC":
            family, principal, assurance = "bridge-engine-b", "svc-bridge-b", "I3"
        elif check_id == "HIDDEN_INFORMATION_FIREWALL":
            family, principal, assurance = "taint-analyzer", "svc-taint", "I2"
        checks.append({
            "check_id": check_id,
            "result": "PASS",
            "verifier_family": family,
            "verifier_version": "pinned-v1",
            "execution_principal": principal,
            "assurance_level": assurance,
            "evidence_sha256": "f" * 64,
            "canon_snapshot_sha256": (
                canon_snapshot_sha if check_id in STATE_DEPENDENT_CHECKS else None
            ),
        })
    return {
        "schema": "video-canon-ai-promotion-v1",
        "policy_version": "school-video-auto-canon-v1",
        "candidate_payload_hash": candidate["payload_hash"],
        "system_profile": "natural-v1",
        "learner_level": "beginner-1",
        "effective_period": {"valid_from": "2026-09-03T00:00:00Z", "valid_to": None},
        "activation_scope": "bidding/natural/v1/response-to-1h",
        "canon_snapshot_sha256": canon_snapshot_sha,
        "rule_test_state_sha256": "b" * 64,
        "checks": checks,
        "rollback": {
            "strategy": "revoke activation and restore prior version",
            "target_knowledge_version_id": None,
            "target_canon_activation_id": None,
            "restore_test_sha256": "a" * 64,
            "result": "PASS",
        },
    }


def test_all_ai_checks_create_sealed_automatic_promotion_command():
    candidate = _candidate()
    result = build_ai_canon_promotion(candidate, _bundle(candidate))

    assert result["status"] == "AUTO_PROMOTION_READY"
    assert result["authority_class"] == "SCHOOL_CANON"
    assert result["human_approval_required"] is False
    assert result["promotion_command"]["operation"] == "ACTIVATE_AI_VERIFIED_VIDEO_CANON"
    assert result["safety"]["world_evidence_used"] is False
    assert len(result["verification_bundle_sha256"]) == 64
    assert result["verification_bundle"]["candidate_payload"] == candidate["payload"]
    assert result["verification_bundle"]["rule_test_state_sha256"] == "b" * 64
    assert result["verification_bundle_canonical_json"]
    assert hashlib.sha256(
        result["verification_bundle_canonical_json"].encode("utf-8")
    ).hexdigest() == result["verification_bundle_sha256"]


@pytest.mark.parametrize(("valid_from", "valid_to", "match"), [
    ("not-a-timestamp", None, "invalid valid_from timestamp"),
    ("2026-09-03T00:00:00", None, "valid_from timestamp must include a UTC offset"),
    ("2026-09-03T00:00:00Z", "not-a-timestamp", "invalid valid_to timestamp"),
    ("2026-09-03T00:00:00Z", "2026-09-02T23:59:59Z", "after valid_from"),
    ("2026-09-03T00:00:00Z", "2026-09-03T00:00:00+00:00", "after valid_from"),
])
def test_effective_period_is_parseable_timezone_aware_and_ordered(
    valid_from, valid_to, match
):
    candidate = _candidate()
    bundle = _bundle(candidate)
    bundle["effective_period"] = {"valid_from": valid_from, "valid_to": valid_to}
    with pytest.raises(VideoCanonAIPromotionError, match=match):
        build_ai_canon_promotion(candidate, bundle)


@pytest.mark.parametrize("mutation, match", [
    (lambda b: b["checks"].pop(), "check set mismatch"),
    (lambda b: b["checks"][0].update(result="FAIL"), "did not pass"),
    (lambda b: next(x for x in b["checks"] if x["check_id"] == "BRIDGE_LOGIC").update(verifier_family="semantic-model-a"), "must be independent"),
    (lambda b: next(x for x in b["checks"] if x["check_id"] == "BRIDGE_LOGIC").update(execution_principal="svc-semantic-a"), "executions must be independent"),
    (lambda b: next(x for x in b["checks"] if x["check_id"] == "CANON_CONFLICT_SCAN").update(canon_snapshot_sha256="d" * 64), "state-dependent check is stale"),
    (lambda b: next(x for x in b["checks"] if x["check_id"] == "HIDDEN_INFORMATION_FIREWALL").update(assurance_level="I1"), "requires I2 or I3"),
    (lambda b: b["rollback"].update(result="FAIL"), "restore test did not pass"),
])
def test_promotion_fails_closed_when_any_ai_gate_is_unproven(mutation, match):
    candidate = _candidate()
    bundle = _bundle(candidate)
    mutation(bundle)
    with pytest.raises(VideoCanonAIPromotionError, match=match):
        build_ai_canon_promotion(candidate, bundle)


@pytest.mark.parametrize("value", [
    1.0001,
    float("nan"),
    float("inf"),
    -float("inf"),
    "0.98",
    True,
])
def test_semantic_confidence_must_be_a_finite_json_number_in_range(value):
    candidate = _candidate()
    candidate["payload"]["semantic_confidence"] = value
    candidate["payload_hash"] = hashlib.sha256(json.dumps(
        candidate["payload"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    with pytest.raises(VideoCanonAIPromotionError, match="finite JSON number"):
        build_ai_canon_promotion(candidate, _bundle(candidate))


def test_verification_cannot_be_replayed_for_changed_candidate():
    candidate = _candidate()
    bundle = _bundle(candidate)
    changed = deepcopy(candidate)
    changed["payload"]["normalized_rule"]["action"] = {"call": "2D"}
    with pytest.raises(VideoCanonAIPromotionError, match="payload hash mismatch"):
        build_ai_canon_promotion(changed, bundle)


def test_conflict_never_auto_promotes():
    candidate = _candidate()
    candidate["payload"]["contradictions"] = ["conflicts with active rule"]
    candidate["payload_hash"] = "0" * 64
    with pytest.raises(VideoCanonAIPromotionError):
        build_ai_canon_promotion(candidate, _bundle(_candidate()))


@pytest.mark.parametrize("field,value", [
    ("ambiguities", None),
    ("contradictions", None),
    ("ambiguities", {}),
    ("contradictions", ""),
])
def test_ambiguity_and_contradiction_arrays_must_be_explicit(field, value):
    candidate = _candidate()
    if value is None:
        candidate["payload"].pop(field)
    else:
        candidate["payload"][field] = value
    candidate["payload_hash"] = hashlib.sha256(json.dumps(
        candidate["payload"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    with pytest.raises(VideoCanonAIPromotionError, match="explicit arrays"):
        build_ai_canon_promotion(candidate, _bundle(candidate))


def test_rollback_target_requires_exact_database_identity():
    candidate = _candidate()
    bundle = _bundle(candidate)
    bundle["rollback"]["target_knowledge_version_id"] = "not-a-uuid"
    with pytest.raises(VideoCanonAIPromotionError, match="invalid rollback target"):
        build_ai_canon_promotion(candidate, bundle)
