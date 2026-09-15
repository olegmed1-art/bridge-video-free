import math

from bridge_contracts.canon_source_domain_policy import (
    EXTERNAL_CHECKS,
    SCHOOL_VIDEO_CHECKS,
    evaluate_canon_source_domain_policy,
)


def _all(checks):
    return {name: True for name in checks}


def test_bidding_is_anchored_in_school_video():
    assert evaluate_canon_source_domain_policy(
        "BIDDING", "SCHOOL_PRIMARY_EVIDENCE", 0.95, _all(SCHOOL_VIDEO_CHECKS)
    )["auto_canon_allowed"]
    denied = evaluate_canon_source_domain_policy(
        "BIDDING", "WORLD_EXTERNAL", 1.0, _all(EXTERNAL_CHECKS)
    )
    assert denied["reason"] == "SOURCE_LANE_NOT_AUTO_CANON"
    assert not denied["canon_mutation_performed"]


def test_authoritative_external_sources_are_allowed_for_card_play_and_defense():
    for domain in ("CARD_PLAY", "DEFENSE"):
        result = evaluate_canon_source_domain_policy(
            domain, "WORLD_EXTERNAL", 0.99, _all(EXTERNAL_CHECKS)
        )
        assert result["auto_canon_allowed"]
        assert result["reason"] == "ELIGIBLE_FOR_CANON_PIPELINE"
        assert not result["canon_mutation_performed"]


def test_external_knowledge_fails_closed_on_conflict_or_interpretation_gap():
    checks = _all(EXTERNAL_CHECKS)
    checks["school_method_conflict_free"] = False
    result = evaluate_canon_source_domain_policy(
        "CARD_PLAY", "WORLD_EXTERNAL", 0.99, checks
    )
    assert not result["auto_canon_allowed"]
    assert result["failed_checks"] == ["school_method_conflict_free"]

    checks = _all(EXTERNAL_CHECKS)
    checks["interpretation_verified"] = "true"
    result = evaluate_canon_source_domain_policy(
        "DEFENSE", "WORLD_EXTERNAL", 0.99, checks
    )
    assert not result["auto_canon_allowed"]
    assert result["failed_checks"] == ["interpretation_verified"]


def test_confidence_and_unknown_routes_fail_closed():
    checks = _all(EXTERNAL_CHECKS)
    for confidence in (0.949999, math.nan, math.inf, "0.99", True):
        assert not evaluate_canon_source_domain_policy(
            "CARD_PLAY", "WORLD_EXTERNAL", confidence, checks
        )["auto_canon_allowed"]
    assert not evaluate_canon_source_domain_policy(
        "SCORING", "WORLD_EXTERNAL", 1.0, checks
    )["auto_canon_allowed"]
