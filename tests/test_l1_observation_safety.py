"""Exercise actual registered evaluators and resolver at their public seams."""
import pytest

from bridge_school_api.l1_canonical_runtime import RuleEvaluation
from bridge_school_api.l1_canonical_runtime_v4 import evaluate, resolve_registered


@pytest.mark.parametrize("rule_id,context", [
    ("RULE-L1-PLAY-NT-COUNT-TOP-TRICKS", {}),
    ("RULE-L1-PLAY-NT-FIND-EXTRA-TRICKS", {}),
    ("RULE-L1-1NT-NATURAL-RESPONSES", {"no_4plus_major": True}),
    ("RULE-L1-WEAK2-RESPONSE-BANDS", {}),
    ("RULE-L1-1NT-STAYMAN-OPENER-RESPONSE", {"auction_1NT_2C_STAYMAN": True}),
    ("RULE-L1-WEAK2-OPEN", {"HCP": 9, "called_suit_length": 6}),
    ("RULE-L1-SCORING-UNDOUBLED-CONTRACT", {"level": 8, "strain": "H"}),
    ("RULE-L1-DISTRIBUTION-POINTS-AFTER-FIT", {"trump_fit": True, "combined_trumps": 9, "hand_role": "long_trump", "singleton": 1}),
    ("RULE-L1-DISTRIBUTION-POINTS-AFTER-FIT", {"trump_fit": True, "combined_trumps": 8, "hand_role": "short_trump", "void": 1}),
    ("RULE-L1-2C-NEGATIVE-OPENER-REBID", {"auction_2C_2D": True, "balanced": True, "HCP": 24}),
])
def test_missing_or_out_of_range_observation_never_manufactures_action(rule_id, context):
    result = evaluate(rule_id, context)
    assert result.status == "NO_MATCH"
    assert result.action is None


@pytest.mark.parametrize("value", [None, True, False, "6", 6.0, 6.5, -1, 41])
def test_hcp_must_be_an_observed_integer(value):
    result = evaluate("RULE-L1-1NT-NATURAL-RESPONSES", {"no_4plus_major": True, "HCP": value})
    assert result.status == "NO_MATCH" and result.action is None


@pytest.mark.parametrize("rule_id,context", [
    ("RULE-L1-DIRECT-OVERCALL-1SUIT", {"HCP": 12, "legal_entry_level": 1, "called_suit_length": 5, "called_suit": "X"}),
    ("RULE-L1-DIRECT-OVERCALL-1SUIT", {"HCP": 12, "legal_entry_level": 1, "called_suit_length": 5, "called_suit": "NT"}),
    ("RULE-L1-SEARCH-4CARD-MAJOR-AFTER-MINOR", {"has_4_card_major": True, "major": "C"}),
])
def test_wrong_denomination_cannot_create_a_bid(rule_id, context):
    result = evaluate(rule_id, context)
    assert result.status == "NO_MATCH" and result.action is None


@pytest.mark.parametrize("context", [
    {"cards": ["ST", "S10", "S9", "S8"], "trump": "NT"},
    {"cards": ["SA", "H2", "S9", "S8"], "lead": "H", "trump": "NT"},
    {"cards": ["S1", "S9", "S8", "S7"], "trump": "NT"},
    {"cards": ["SA", "S9", "S8", "S7"]},
])
def test_incomplete_or_contradictory_trick_has_no_winner(context):
    result = evaluate("RULE-L1-TRICK-WINNER", context)
    assert result.status == "NO_MATCH" and result.action is None


def test_nt_constraint_filters_before_ranking_and_does_not_veto_suit():
    context = {"opponent_suit_known": True, "considering_NT": True,
               "stopper_in_opponent_suit": False, "HCP": 13,
               "no_preferred_own_suit": True, "no_minor_fit": True}
    block = evaluate("RULE-L1-NT-STOPPER-REQUIRED-COMPETITION", context)
    nt = evaluate("RULE-L1-MINOR-NT-RESPONSE-BANDS", context)
    assert block.status == "BLOCK" and nt.action == "3NT"
    for values in ([block, nt], [nt, block]):
        result = resolve_registered(values)
        assert result.status == "BLOCK" and result.action != "3NT"
    suit = evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 4})
    result = resolve_registered([block, nt, suit])
    assert result.status == "MATCH" and result.action == "1H"


def test_unknown_stopper_cannot_approve_nt_but_false_applicability_can_short_circuit():
    nt = evaluate("RULE-L1-MINOR-NT-RESPONSE-BANDS",
                  {"HCP": 13, "no_preferred_own_suit": True, "no_minor_fit": True})
    unknown = evaluate("RULE-L1-NT-STOPPER-REQUIRED-COMPETITION",
                       {"opponent_suit_known": True, "considering_NT": True})
    assert unknown.status == "BLOCK" and unknown.action is None
    result = resolve_registered([unknown, nt])
    assert result.status == "BLOCK" and result.action is None
    inapplicable = evaluate("RULE-L1-NT-STOPPER-REQUIRED-COMPETITION", {"considering_NT": False})
    assert inapplicable.status == "NO_MATCH" and inapplicable.action is None
    for malformed in (None, [], ["bad"]):
        missing = evaluate("RULE-L1-NT-STOPPER-REQUIRED-COMPETITION", malformed)
        withheld = resolve_registered([missing, nt])
        assert withheld.status == "BLOCK" and withheld.action is None
    explicit = evaluate("RULE-L1-NT-STOPPER-REQUIRED-COMPETITION",
                        {"opponent_suit_known": True, "considering_NT": True, "stopper_in_opponent_suit": False})
    lesson = evaluate("RULE-L1-LESSON2-RESPONDER-AFTER-1M",
                      {"lesson_stage": 2, "partner_opened_1M": True, "HCP": 7, "support": 0})
    assert lesson.action == "LESSON2_NT_BRANCH"
    assert resolve_registered([explicit, lesson]).status == "BLOCK"


def test_reference_block_is_not_a_global_veto_and_ties_remain_conflicts():
    reference = evaluate("RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE", {})
    one = RuleEvaluation("synthetic-one", "MATCH", "2H", 100, 2, 2)
    two = RuleEvaluation("synthetic-two", "MATCH", "2S", 100, 2, 2)
    assert resolve_registered([reference, one]).action == "2H"
    conflict = resolve_registered([one, two])
    assert conflict.status == "CANON_CONFLICT" and conflict.action is None


def test_explicit_zero_and_ten_remain_valid_observations():
    zero = evaluate("RULE-L1-PLAY-NT-COUNT-TOP-TRICKS", {"top_tricks": 0})
    assert zero.status == "MATCH" and zero.action == 0
    for ten in ("ST", "S10"):
        winner = evaluate("RULE-L1-TRICK-WINNER", {"cards": [ten, "S9", "S8", "S7"], "trump": "NT"})
        assert winner.status == "MATCH" and winner.action == ten


@pytest.mark.parametrize("module_name", [
    "l1_canonical_runtime", "l1_canonical_runtime_v2", "l1_canonical_runtime_v3", "l1_canonical_runtime_v4",
])
def test_profile_isolation_precedes_context_parsing(module_name):
    import sys
    api = sys.modules["bridge_school_api." + module_name]
    result = api.evaluate("RULE-L1-HCP-COUNT", None, system_version="SCHOOL_TOURNAMENT_CURRENT_V1")
    assert result.status == "NO_MATCH" and result.action is None
