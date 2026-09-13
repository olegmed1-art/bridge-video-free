from bridge_school_api.l1_canonical_registry import (
    ACTIVE_DOMAIN_RULE_IDS,
    ALL_SKILL_IDS,
    APPROVED_SKILL_IDS,
    BLOCKED_SKILL_IDS,
    EXPECTED_RULE_ID_FINGERPRINT,
    GOVERNANCE_RULE_IDS,
    PARTIAL_SKILL_IDS,
    RULE_ID_FINGERPRINT,
    classify_rule,
)
from bridge_school_api.l1_canonical_runtime_v2 import evaluate, runtime_status


def test_live_l1_registry_counts_match_drive_integrity_gate():
    assert len(ACTIVE_DOMAIN_RULE_IDS) == 111
    assert len(set(ACTIVE_DOMAIN_RULE_IDS)) == 111
    assert len(GOVERNANCE_RULE_IDS) == 2
    assert len(ALL_SKILL_IDS) == 121
    assert len(APPROVED_SKILL_IDS) == 103
    assert len(PARTIAL_SKILL_IDS) == 17
    assert len(BLOCKED_SKILL_IDS) == 1
    assert BLOCKED_SKILL_IDS == {"SKILL-0081"}
    assert not (PARTIAL_SKILL_IDS & BLOCKED_SKILL_IDS)


def test_registry_fingerprint_is_pinned():
    assert RULE_ID_FINGERPRINT == EXPECTED_RULE_ID_FINGERPRINT
    assert RULE_ID_FINGERPRINT == "143d7387734a721ebedd6cab78af21278f7b250d963ee2c82562e50dc3bbfd33"


def test_every_registered_domain_rule_is_known_to_runtime_v2():
    for rule_id in ACTIVE_DOMAIN_RULE_IDS:
        assert classify_rule(rule_id) == "DOMAIN"
        status = runtime_status(rule_id)
        assert status["known"] is True
        assert status["category"] == "DOMAIN"
        result = evaluate(rule_id, {})
        assert not (
            result.status == "BLOCK" and result.action == "UNKNOWN_RULE_ID"
        ), rule_id


def test_both_governance_rules_are_registered():
    assert set(GOVERNANCE_RULE_IDS) == {
        "RULE-L1-MACHINE-COMPILER-GATE",
        "RULE-L1-RUNTIME-CONFLICT-GATE",
    }
    for rule_id in GOVERNANCE_RULE_IDS:
        assert classify_rule(rule_id) == "GOVERNANCE"
        assert runtime_status(rule_id)["known"] is True


def test_unknown_or_tournament_rules_fail_closed():
    for rule_id in (
        "RULE-TOUR-OPEN-1C",
        "RULE-L1-DEFENSE-SIGNAL-GATE",
        "RULE-L1-TYPO-NOT-IN-CANON",
    ):
        result = evaluate(rule_id, {})
        assert result.status == "BLOCK"
        assert result.action == "UNKNOWN_RULE_ID"


def test_wrong_system_version_is_isolated_before_rule_execution():
    result = evaluate(
        "RULE-L1-OPEN-1H",
        {"HCP": 13, "H": 5, "S": 4},
        system_version="TOURNAMENT_DB_V1",
    )
    assert result.status == "NO_MATCH"
    assert "system isolation" in (result.reason or "")


def test_v2_preserves_proven_core_semantics():
    one_heart = evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 4})
    assert one_heart.status == "MATCH"
    assert one_heart.action == "1H"

    five_five = evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 5})
    assert five_five.status == "NO_MATCH"

    one_spade = evaluate("RULE-L1-OPEN-1S", {"HCP": 13, "H": 5, "S": 5})
    assert one_spade.status == "MATCH"
    assert one_spade.action == "1S"

    stopper = evaluate(
        "RULE-L1-TAKEOUT-DOUBLE-NT-OVER-MINOR",
        {"HCP": 8, "stopper": True, "minor_option": True},
    )
    assert stopper.status == "MATCH"
    assert stopper.action == "PREFER_NT_OVER_MINOR"


def test_known_but_not_semantically_encoded_rule_fails_closed_not_unknown():
    result = evaluate(
        "RULE-L1-CONTRACT-SUCCESS",
        {"declarer_tricks": 9, "required_tricks": 9},
    )
    assert result.status == "BLOCK"
    assert result.action == "KNOWN_RULE_NOT_EXECUTABLE"
    assert "fails closed" in (result.reason or "")
