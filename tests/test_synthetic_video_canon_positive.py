"""Structural coverage of a fictional 0329 rehearsal handoff, with no SQL I/O."""
from copy import deepcopy

import pytest

from bridge_contracts.video_canon_ai_promotion import (
    REQUIRED_CHECKS,
    STATE_DEPENDENT_CHECKS,
    VideoCanonAIPromotionError,
    build_ai_canon_promotion,
)
from tests.fixtures.synthetic_video_canon_positive import (
    SYNTHETIC_LOGIN_PRINCIPALS,
    build_synthetic_positive_rehearsal,
)


def test_full_synthetic_handoff_is_bound_but_remains_non_authoritative():
    fixture = build_synthetic_positive_rehearsal(
        "c" * 64, "b" * 64, SYNTHETIC_LOGIN_PRINCIPALS
    )
    candidate = fixture["candidate"]
    command = fixture["sealed_command"]
    checks = fixture["verification_rows"]
    assert fixture["fixture_class"] == "SYNTHETIC_NON_AUTHORITATIVE"
    assert fixture["real_rights_or_review_proven"] is False
    assert candidate["promotion_status"] == "STAGING_ONLY"
    assert command["status"] == "AUTO_PROMOTION_READY"
    assert "canon_activation_id" not in command
    assert {row["check_id"] for row in checks} == REQUIRED_CHECKS
    assert len(checks) == len(REQUIRED_CHECKS)
    assert {row["execution_principal"] for row in checks} == {
        "canon_rehearsal_semantic_login",
        "canon_rehearsal_bridge_login",
        "canon_rehearsal_firewall_login",
        "canon_rehearsal_control_login",
    }
    assert len(set(fixture["capability_grants"].values())) == 4
    assert all(
        row["canon_snapshot_sha256"] == ("c" * 64 if row["check_id"] in STATE_DEPENDENT_CHECKS else None)
        for row in checks
    )
    assert {row["test_type"] for row in fixture["rule_test_claims"]} == {
        "positive", "negative", "boundary", "interference", "hidden_information", "regression"
    }
    assert fixture["source_policy_claim"]["source_sha256"] == candidate["payload"]["source"]["source_sha256"]
    assert fixture["knowledge_version_claim"]["content"] == candidate["payload"]
    assert fixture["knowledge_version_claim"]["review_status"] == "unreviewed"
    assert fixture["knowledge_version_source_claim"]["source_locator"] == {
        "transcript_locators": candidate["payload"]["teacher_assertion"]["transcript_locators"]
    }


def test_rehearsal_needs_fresh_database_state_hashes():
    with pytest.raises(ValueError, match="disposable state hashes"):
        build_synthetic_positive_rehearsal("unknown", "b" * 64, SYNTHETIC_LOGIN_PRINCIPALS)
    fixture = build_synthetic_positive_rehearsal(
        "c" * 64, "b" * 64, SYNTHETIC_LOGIN_PRINCIPALS
    )
    stale = deepcopy(fixture["review_input"])
    stale["canon_snapshot_sha256"] = "d" * 64
    with pytest.raises(VideoCanonAIPromotionError, match="state-dependent check is stale"):
        build_ai_canon_promotion(fixture["candidate"], stale)


def test_login_principal_change_reseals_digest_and_collisions_are_rejected():
    first = build_synthetic_positive_rehearsal(
        "c" * 64, "b" * 64, SYNTHETIC_LOGIN_PRINCIPALS
    )
    changed = {**SYNTHETIC_LOGIN_PRINCIPALS, "semantic": "canon_rehearsal_semantic_b_login"}
    second = build_synthetic_positive_rehearsal("c" * 64, "b" * 64, changed)
    assert first["sealed_command"]["verification_bundle_sha256"] != second["sealed_command"]["verification_bundle_sha256"]
    duplicate = {**SYNTHETIC_LOGIN_PRINCIPALS, "semantic": SYNTHETIC_LOGIN_PRINCIPALS["control"]}
    with pytest.raises(ValueError, match="four distinct"):
        build_synthetic_positive_rehearsal("c" * 64, "b" * 64, duplicate)
