from __future__ import annotations

import pytest

from bridge_school_api.l1_canonical_registry import ACTIVE_DOMAIN_RULE_IDS
from bridge_school_api.l1_canonical_runtime_v4 import EXTRA_PROCEDURAL_RULE_IDS, evaluate


@pytest.mark.parametrize(
    ("rule_id", "context", "expected"),
    [
        ("RULE-L1-AUCTION-ORDER-FINAL-CONTRACT", {"dealer_known": True, "passes_after_last_significant_call": 3, "last_significant_call": "4H"}, {"dealer_starts": True, "clockwise": True, "bids_must_increase": True, "final_contract": "4H"}),
        ("RULE-L1-CUEBID-AFTER-TAKEOUT-CONTINUE", {"partner_answered_cuebid": True, "game_force_active": True, "has_4_card_major": True}, "SHOW_4CARD_MAJOR"),
        ("RULE-L1-DEAL-STAGES", {}, ("BIDDING", "CONTRACT", "PLAY")),
        ("RULE-L1-DECK-RANK-SUIT-HIERARCHY", {}, {"deck_size": 52, "ranks": ("A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2"), "suits": ("S", "H", "D", "C"), "majors": ("S", "H"), "minors": ("D", "C")}),
        ("RULE-L1-DECLARER-ROLE", {"final_denomination": "H", "winning_side": ["N", "S"], "calls": [{"seat": "N", "call": "1H"}, {"seat": "E", "call": "2C"}, {"seat": "S", "call": "4H"}]}, {"declarer": "N", "dummy": "S", "defenders": ("E", "W")}),
        ("RULE-L1-DIRECT-OVERCALL-1S", {"legal_entry_level": 1, "HCP": 12, "S": 5}, "1S"),
        ("RULE-L1-DOUBLE-INITIAL-AMBIGUITY", {"partner_just_doubled_opening": True, "partner_rebid_seen": False}, "RESPOND_COMMON_SCHEME_FIRST;DEFER_VARIANT_CLASSIFICATION"),
        ("RULE-L1-DOUBLE-MEANING-BY-CONTEXT", {"direct_double_of_opening": True}, "TAKEOUT_OR_STRONG_L1"),
        ("RULE-L1-JUMP-OVERCALL-PREEMPT", {"HCP": 8, "called_suit": "D", "called_suit_length": 7, "called_suit_has_at_least_two_honors": True}, "3D"),
        ("RULE-L1-LESSON2-RESPONDER-AFTER-1M", {"lesson_stage": 2, "partner_opened_1M": True, "HCP": 7, "support": 3}, "RAISE"),
        ("RULE-L1-OPENER-BALANCE-AFTER-RESPONSE", {"partner_response_range_known": True, "HCP": 16}, "COMBINE_RANGES_THEN_CHOOSE_STOP_OR_ACCEPT_INVITE_OR_GAME"),
        ("RULE-L1-OPENER-REBID-PURPOSE", {"partner_response_known": True}, "DESCRIBE_ADDITIONAL_STRENGTH_AND_SHAPE_INFORMATION"),
        ("RULE-L1-OPENER-REPEAT-OWN-SUIT-6PLUS", {"fit_not_found": True, "opening_suit_length": 6}, "REPEAT_OPENING_SUIT"),
        ("RULE-L1-OPENER-SUPPORT-RESPONDER-NEW-SUIT", {"partner_response_new_suit": True, "own_support_in_responder_suit": 4}, "SHOW_4PLUS_FIT"),
        ("RULE-L1-OVERCALL-2PLUS-LEVEL", {"legal_entry_level": 3, "called_suit": "H", "called_suit_length": 5, "HCP": 12, "strong_suit_exception": True}, "3H"),
        ("RULE-L1-PLAY-AVOID-BLOCKING", {"entry_or_access_risk": True}, "CHOOSE_ACCESS_PRESERVING_ORDER"),
        ("RULE-L1-PLAY-DISCARD-LOSER-ON-WINNER", {"loser_identified": True, "side_suit_winner_available_or_developable": True}, "PLAN_DISCARD_LOSER_ON_SIDE_WINNER"),
        ("RULE-L1-PLAY-DRAW-TRUMPS-TIMING", {"defender_trumps_remaining": True, "required_loss_elimination_first": True}, "DELAY_TRUMP_DRAW_FOR_REQUIRED_LOSS_ELIMINATION"),
        ("RULE-L1-PLAY-ESTABLISH-LONG-SUIT", {"long_suit_can_produce_extra_tricks": True}, "DEVELOP_LONG_SUIT_BEFORE_UNRELATED_CASHES_WHEN_REQUIRED"),
        ("RULE-L1-PLAY-EXPASS-PLAN", {"expass_position_recognized": True, "key_honor_with_relevant_defender": True, "contract_trump": True}, "PLAN_EXPASSE;ACCOUNT_FOR_POSSIBLE_RUFF"),
        ("RULE-L1-PLAY-FINESSE-PLAN", {"finesse_position_recognized": True, "missing_honor_location_relevant": True}, "EVALUATE_FINESSE_DIRECTION_AND_ENTRIES"),
        ("RULE-L1-PLAY-PRESERVE-ENTRIES", {"planned_target_hand_known": True}, "IDENTIFY_AND_PRESERVE_REQUIRED_ENTRY"),
        ("RULE-L1-PLAY-RUFF-LOSER-SHORT-TRUMP", {"loser_identified": True, "short_trump_hand_has_available_trump": True, "ruff_gains_trick": True}, "PLAN_RUFF_LOSER_IN_SHORT_TRUMP_HAND"),
        ("RULE-L1-PREEMPT-PURPOSE", {"weak_hand_with_long_suit": True}, "USE_PREEMPT_FOR_SPACE_DENIAL_WITHIN_DEFINED_CONSTRAINTS"),
        ("RULE-L1-RESPONDER-REEVALUATE-AFTER-OPENER-REBID", {"opener_rebid_received": True, "new_opener_range_or_shape_info_available": True}, "RECOMPUTE_COMBINED_PICTURE_THEN_CHOOSE_STOP_INVITE_GAME"),
        ("RULE-L1-SHAPE-CLASSIFICATION", {"S": 5, "H": 4, "D": 2, "C": 2}, {"shape_class": "SEMIBALANCED_TWO_DOUBLETONS", "voids": 0, "singletons": 0, "doubletons": 2}),
        ("RULE-L1-STRAIN-PRIORITY", {"multiple_suitable_contracts": True, "lesson_supported_exception": False}, "MAJOR>NT>MINOR"),
        ("RULE-L1-STRONG2C-SUIT-REBID-CONTINUE", {"strong2C_opener_rebid_suit": True, "fit": False, "own_suit_length": 5}, "SHOW_OWN_SUIT"),
        ("RULE-L1-TABLE-CARD-ORIENTATION", {}, {"use_bidding_box": True, "won_trick": "VERTICAL", "lost_trick": "HORIZONTAL"}),
        ("RULE-L1-TAKEOUT-DOUBLE-REBID-BALANCE", {"takeout_doubler": True, "partner_responded": True, "doubler_range": "13-17"}, "COMBINE_RANGES_THEN_PASS_OR_GAME"),
    ],
)
def test_v4_source_explicit_positive(rule_id: str, context: dict, expected: object) -> None:
    result = evaluate(rule_id, context)
    assert result.status == "MATCH"
    assert result.action == expected


def test_v4_wave_is_exact_and_canonical() -> None:
    assert len(EXTRA_PROCEDURAL_RULE_IDS) == 30
    assert EXTRA_PROCEDURAL_RULE_IDS <= set(ACTIVE_DOMAIN_RULE_IDS)


def test_shape_classification_balanced_and_shortness() -> None:
    balanced = evaluate("RULE-L1-SHAPE-CLASSIFICATION", {"S": 5, "H": 3, "D": 3, "C": 2})
    assert balanced.action["shape_class"] == "BALANCED"
    short = evaluate("RULE-L1-SHAPE-CLASSIFICATION", {"S": 4, "H": 4, "D": 4, "C": 1})
    assert short.action["shape_class"] == "UNBALANCED_SHORTNESS"
    assert short.action["singletons"] == 1


def test_strain_priority_defers_explicit_exception() -> None:
    result = evaluate("RULE-L1-STRAIN-PRIORITY", {"multiple_suitable_contracts": True, "lesson_supported_exception": True})
    assert result.status == "NO_MATCH"


def test_overcall_three_level_without_exception_needs_14() -> None:
    result = evaluate("RULE-L1-OVERCALL-2PLUS-LEVEL", {"legal_entry_level": 3, "called_suit": "H", "called_suit_length": 5, "HCP": 12, "strong_suit_exception": False})
    assert result.status == "NO_MATCH"


def test_competitive_strength_principle_remains_fail_closed() -> None:
    result = evaluate("RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE", {"aggression_increase": True})
    assert result.status == "BLOCK"
    assert result.action == "KNOWN_RULE_NOT_EXECUTABLE"


def test_tournament_rule_still_blocked() -> None:
    result = evaluate("RULE-TOUR-OPEN-1NT", {"HCP": 16})
    assert result.status == "BLOCK"
    assert result.action == "UNKNOWN_RULE_ID"
