from __future__ import annotations

import pytest

from bridge_school_api.l1_canonical_registry import ACTIVE_DOMAIN_RULE_IDS
from bridge_school_api.l1_canonical_runtime_v3 import (
    EXTRA_SOURCE_EXPLICIT_RULE_IDS,
    evaluate,
)


@pytest.mark.parametrize(
    ("rule_id", "context", "expected"),
    [
        ("RULE-L1-CONTRACT-SUCCESS", {"declarer_tricks": 9, "required_tricks": 9}, True),
        ("RULE-L1-OPEN-PASS-THRESHOLD", {"lesson_stage": 2, "ordinary_one_level_opening_model": True, "HCP": 11}, "PASS_IN_LESSON2_BRANCH"),
        ("RULE-L1-MAJOR-FIT-DETECT", {"partner_major_length": 5, "responder_support": 3}, True),
        ("RULE-L1-GAME-BALANCE-25", {"combined_HCP": 25}, True),
        ("RULE-L1-RESPONDER-STRENGTH-BANDS", {"HCP": 12}, "INVITE"),
        ("RULE-L1-MINOR-OPEN-CHOICE", {"minor_opening_path": True, "C": 4, "D": 4}, "1C"),
        ("RULE-L1-MINOR-SUPPORT-BANDS", {"partner_minor": "D", "no_preferred_own_suit": True, "support_in_partner_minor": 5, "HCP": 11}, "3D"),
        ("RULE-L1-MINOR-NT-RESPONSE-BANDS", {"no_preferred_own_suit": True, "no_minor_fit": True, "HCP": 13}, "3NT"),
        ("RULE-L1-SHOW-MAJOR-FIT-FIRST", {"responder_support": 3}, "RAISE_PARTNER_MAJOR"),
        ("RULE-L1-NEW-SUIT-MIN-LENGTH", {"called_suit_length": 4}, "ALLOWED"),
        ("RULE-L1-NEW-SUIT-FORCES-OPENER", {"current_L1_context": True, "partner_response_new_natural_suit": True}, "OPENER_MUST_CONTINUE"),
        ("RULE-L1-2C-NEGATIVE-2D", {"partner_opened_2C": True, "HCP": 7}, "2D"),
        ("RULE-L1-WEAK3-OPEN", {"called_suit": "H", "called_suit_length": 7, "HCP": 9}, "3H"),
        ("RULE-L1-WEAK4-OPEN", {"called_suit": "S", "called_suit_length": 8, "HCP": 8}, "4S"),
        ("RULE-L1-REVALUE-AFTER-FIT", {"trump_fit": True, "HCP": 12, "distribution_points": 3}, 15),
        ("RULE-L1-MINOR-OPENING-PATH", {"HCP": 14, "no_5_card_major": True, "not_1NT_opening": True, "not_2NT_opening": True}, "MINOR_OPENING_PATH"),
        ("RULE-L1-MINOR-RESPONSE-PRIORITY", {}, "OWN_PREFERRED_SUIT>MINOR_FIT>NT"),
        ("RULE-L1-OPENER-SECOND-SUIT-REBID", {"fit_not_found": True, "second_suit_length": 4, "HCP": 18}, "SECOND_SUIT_JUMP"),
        ("RULE-L1-OPENER-NT-REBID", {"no_fit": True, "no_second_4plus_suit": True, "opening_suit_length": 5, "HCP": 14}, "NT_NO_JUMP"),
        ("RULE-L1-2C-NEGATIVE-OPENER-REBID", {"auction_2C_2D": True, "balanced": True, "HCP": 24}, "2NT"),
        ("RULE-L1-RESPONSE-TO-1LEVEL-OVERCALL-BANDS", {"HCP": 15}, "MAXIMUM"),
        ("RULE-L1-RESPONSE-TO-2LEVEL-OVERCALL-BANDS", {"HCP": 11}, "INVITE"),
        ("RULE-L1-NT-STOPPER-REQUIRED-COMPETITION", {"opponent_suit_known": True, "considering_NT": True, "stopper_in_opponent_suit": True}, "NT_ALLOWED_BY_STOPPER_GATE"),
        ("RULE-L1-STRONG-DOUBLE-REVEAL-REBID", {"initial_call_double": True, "partner_responded": True, "doubler_makes_contentful_rebid": True}, "INFER_STRONG_DOUBLE_18PLUS"),
        ("RULE-L1-STRONG-DOUBLE-RAISE-BANDS", {"partner_suit_fit": True, "HCP": 23}, "JUMP_RAISE"),
        ("RULE-L1-1NT-OVERCALL-INHERITS-1NT-RESPONSES", {"partner_overcalled_1NT": True, "partner_range": "15-17"}, "INHERIT_OPENING_1NT_L1"),
        ("RULE-L1-2NT-OVERCALL-RESPONSE", {"partner_overcalled_2NT": True, "partner_range": "15-17", "HCP": 9}, "GAME_SEARCH"),
        ("RULE-L1-PREEMPT-OVERCALL-RESPONSE", {"partner_made_preemptive_overcall": True, "HCP": 16}, "INVITE"),
        ("RULE-L1-3LEVEL-OVERCALL-RESPONSE", {"partner_natural_overcall_level_3": True, "HCP": 10}, "GAME_SEARCH"),
        ("RULE-L1-STRONG-DOUBLE-OWN-SUIT-REBID", {"fit_to_partner": False, "own_suit_length": 5, "HCP": 22}, "OWN_SUIT_JUMP"),
        ("RULE-L1-STRONG-DOUBLE-NT-REBID", {"fit_to_partner": False, "own_5plus_suit": False, "stopper_in_opponent_suit": True, "HCP": 25}, "3NT"),
        ("RULE-L1-HCP-CONTRACT-LEVEL-GUIDE", {"combined_HCP": 34, "mode": "NT"}, "6NT"),
        ("RULE-L1-1NT-STAYMAN-OPENER-RESPONSE", {"auction_1NT_2C_STAYMAN": True, "H": 4, "S": 4}, "SHOW_LOWER_MAJOR"),
        ("RULE-L1-1NT-STAYMAN-RESPONDER-CONTINUE", {"stayman_response_received": True, "fit_found": False, "HCP": 10}, "GAME_NT"),
        ("RULE-L1-1M-NT-RESPONSE-BANDS-CONTEXT", {"no_fit": True, "no_preferred_own_suit_in_current_branch": True, "HCP": 11}, "2NT"),
        ("RULE-L1-PREFER-3NT-OVER-5MINOR-BASIC", {"minor_fit_context": True, "game_balance_reached": True}, "PREFER_3NT_OVER_5MINOR"),
        ("RULE-L1-OPENER-PASS-AFTER-RESPONSE-FORCING-CHECK", {"partner_response_type": "NEW_SUIT"}, "PASS_FORBIDDEN"),
        ("RULE-L1-NATURAL-VS-ARTIFICIAL-ALERT", {"artificial_call": True}, "ALERT_IN_LESSON_SCOPE"),
        ("RULE-L1-WEAK2-OPENER-ACCEPT-INVITE", {"partner_invited": True, "opener_range": "7-11", "sufficient_combined_balance": False}, "STOP"),
        ("RULE-L1-PENALTY-PASS-ON-DOUBLE-CONTEXT", {"considering_pass": True, "opponent_opened_in_responder_strong_suit": True, "HCP": 6}, "PASS_CONVERTS_TO_PENALTY"),
    ],
)
def test_v3_source_explicit_positive(rule_id: str, context: dict, expected: object) -> None:
    result = evaluate(rule_id, context)
    assert result.status == "MATCH"
    assert result.action == expected


def test_v3_registry_wave_is_exact_and_canonical() -> None:
    assert len(EXTRA_SOURCE_EXPLICIT_RULE_IDS) == 40
    assert EXTRA_SOURCE_EXPLICIT_RULE_IDS <= set(ACTIVE_DOMAIN_RULE_IDS)


def test_minor_open_choice_explicit_ties_only() -> None:
    assert evaluate("RULE-L1-MINOR-OPEN-CHOICE", {"minor_opening_path": True, "C": 5, "D": 5}).action == "1D"
    result = evaluate("RULE-L1-MINOR-OPEN-CHOICE", {"minor_opening_path": True, "C": 2, "D": 2})
    assert result.status == "NO_MATCH"


def test_nt_rebid_does_not_fill_15_17_gap() -> None:
    result = evaluate(
        "RULE-L1-OPENER-NT-REBID",
        {"no_fit": True, "no_second_4plus_suit": True, "opening_suit_length": 5, "HCP": 16},
    )
    assert result.status == "NO_MATCH"


def test_competitive_nt_without_stopper_is_blocked() -> None:
    result = evaluate(
        "RULE-L1-NT-STOPPER-REQUIRED-COMPETITION",
        {"opponent_suit_known": True, "considering_NT": True, "stopper_in_opponent_suit": False},
    )
    assert result.status == "BLOCK"
    assert result.action == "NT_FORBIDDEN_NO_STOPPER"


def test_stayman_both_majors_starts_with_lower_major_semantics() -> None:
    result = evaluate(
        "RULE-L1-1NT-STAYMAN-OPENER-RESPONSE",
        {"auction_1NT_2C_STAYMAN": True, "H": 4, "S": 5},
    )
    assert result.status == "MATCH"
    assert result.action == "SHOW_LOWER_MAJOR"


def test_v3_preserves_v2_fail_closed_for_unbounded_rule() -> None:
    result = evaluate("RULE-L1-STRAIN-PRIORITY", {"multiple_suitable_contracts": True})
    assert result.status == "BLOCK"
    assert result.action == "KNOWN_RULE_NOT_EXECUTABLE"


def test_v3_preserves_system_isolation() -> None:
    result = evaluate("RULE-L1-GAME-BALANCE-25", {"combined_HCP": 25}, system_version="SCHOOL_TOURNAMENT_CURRENT_V1")
    assert result.status == "NO_MATCH"


def test_v3_unknown_tournament_rule_is_blocked() -> None:
    result = evaluate("RULE-TOUR-OPEN-1NT", {"HCP": 16})
    assert result.status == "BLOCK"
    assert result.action == "UNKNOWN_RULE_ID"
