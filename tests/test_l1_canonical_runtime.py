import pytest

from bridge_school_api.l1_canonical_runtime import evaluate, resolve


def test_hcp_values():
    assert evaluate("RULE-L1-HCP-COUNT", {"A": 1, "K": 1, "Q": 1, "J": 1}).action == 10


def test_contract_required_tricks():
    assert evaluate("RULE-L1-CONTRACT-REQUIRED-TRICKS", {"contract_level": 3}).action == 9


def test_trick_winner_nt():
    assert evaluate("RULE-L1-TRICK-WINNER", {"trump": "NT", "lead": "S", "cards": ["SK", "S9", "HA", "SQ"]}).action == "SK"


def test_trick_winner_trump():
    assert evaluate("RULE-L1-TRICK-WINNER", {"trump": "H", "lead": "S", "cards": ["SA", "H2", "HQ", "SK"]}).action == "HQ"


def test_open_1h():
    assert evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 4}).action == "1H"


def test_55_major_priority():
    h = evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 5})
    s = evaluate("RULE-L1-OPEN-1S", {"HCP": 13, "H": 5, "S": 5})
    assert h.status == "NO_MATCH"
    assert s.action == "1S"


def test_open_1nt():
    assert evaluate("RULE-L1-OPEN-1NT", {"HCP": 16, "shape": "4333", "no_5_card_major": True}).action == "1NT"


def test_open_1nt_rejects_5_major():
    assert evaluate("RULE-L1-OPEN-1NT", {"HCP": 16, "shape": "5332", "S": 5, "no_5_card_major": False}).status == "NO_MATCH"


def test_open_2nt():
    assert evaluate("RULE-L1-OPEN-2NT", {"HCP": 21, "shape": "4333", "balanced": True, "no_5_card_major": True}).action == "2NT"


def test_weak2_specificity_vs_early_pass_model():
    result = evaluate("RULE-L1-WEAK2-OPEN", {"HCP": 9, "called_suit": "H", "called_suit_length": 6})
    assert result.action == "2H"


def test_strong_2c_hcp_branch():
    assert evaluate("RULE-L1-OPEN-2C-HCP-BRANCH", {"HCP": 23}).action == "2C"


@pytest.mark.parametrize("major,support,hcp,expected", [("H", 3, 8, "2H"), ("S", 3, 11, "3S"), ("S", 4, 13, "4S")])
def test_major_raise_bands(major, support, hcp, expected):
    assert evaluate("RULE-L1-MAJOR-RAISE-BANDS", {"major": major, "support": support, "HCP": hcp}).action == expected


def test_search_four_card_major_after_minor():
    assert evaluate("RULE-L1-SEARCH-4CARD-MAJOR-AFTER-MINOR", {"has_4_card_major": True, "major": "H"}).action == "1H"


def test_new_suit_level2_requires_11():
    assert evaluate("RULE-L1-NEW-SUIT-HCP-BY-LEVEL", {"level": 2, "HCP": 10}).status == "NO_MATCH"


def test_fallback_1nt():
    assert evaluate("RULE-L1-FALLBACK-1NT-6-10", {"HCP": 8, "no_fit": True, "own_suit_requires_level2": True}).action == "1NT"


def test_jump_new_suit():
    assert evaluate("RULE-L1-JUMP-NEW-SUIT-13PLUS-5PLUS", {"jump_new_suit": True, "HCP": 13, "suit_length": 5}).matched


def test_1nt_natural_pass():
    assert evaluate("RULE-L1-1NT-NATURAL-RESPONSES", {"HCP": 6, "no_4plus_major": True}).action == "PASS"


def test_stayman():
    assert evaluate("RULE-L1-1NT-STAYMAN", {"HCP": 8, "has_4_card_major": True, "has_5plus_major": False}).action == "2C_STAYMAN"


def test_transfer_entry():
    action = evaluate("RULE-L1-1NT-TRANSFER-ENTRY-GENERIC", {"HCP": 6, "target_major": "S", "S": 5}).action
    assert action == "TRANSFER_ONE_STEP_BELOW_S;OPENER_MUST_BID_S"


def test_transfer_invite_exact5():
    assert evaluate("RULE-L1-1NT-TRANSFER-SECOND-BID", {"HCP": 8, "target_major_length": 5}).action == "INVITE;NT_CHOICE_BRANCH"


def test_transfer_game_6plus():
    assert evaluate("RULE-L1-1NT-TRANSFER-SECOND-BID", {"HCP": 10, "target_major_length": 6}).action == "GAME;MAJOR_BRANCH"


def test_2nt_pass_0_4():
    assert evaluate("RULE-L1-2NT-GAME-BALANCE", {"HCP": 4, "no_major_action": True}).action == "PASS"


def test_2nt_stayman_shift():
    assert evaluate("RULE-L1-2NT-TRANSFER-STAYMAN-SHIFT", {"HCP": 5, "has_4_card_major": True}).action == "STAYMAN_AT_3_LEVEL"


def test_direct_overcall():
    assert evaluate("RULE-L1-DIRECT-OVERCALL-1SUIT", {"legal_entry_level": 1, "HCP": 12, "called_suit": "S", "called_suit_length": 5}).action == "1S"


def test_takeout_double():
    ctx = {"opponent_opened": True, "HCP": 14, "each_unbid_suit": 3, "no_suitable_suit_overcall": True, "no_suitable_NT": True}
    assert evaluate("RULE-L1-TAKEOUT-DOUBLE", ctx).action == "DOUBLE_TAKEOUT"


def test_strong_double():
    assert evaluate("RULE-L1-STRONG-DOUBLE-18PLUS", {"opponent_opened": True, "HCP": 19}).action == "DOUBLE_STRONG"


def test_double_suit_response_minimum():
    assert evaluate("RULE-L1-DOUBLE-SUIT-RESPONSE-BANDS", {"HCP": 7, "best_suit_length": 4}).action == "SUIT_NEAREST"


def test_double_suit_response_invite():
    assert evaluate("RULE-L1-DOUBLE-SUIT-RESPONSE-BANDS", {"HCP": 10, "best_suit_length": 4}).action == "JUMP_SUIT"


def test_double_suit_response_game_major():
    assert evaluate("RULE-L1-DOUBLE-SUIT-RESPONSE-BANDS", {"HCP": 12, "major_length": 5}).action == "4M"


def test_double_nt_over_minor_priority():
    nt = evaluate("RULE-L1-DOUBLE-NT-RESPONSE-BANDS", {"HCP": 8, "stopper": True})
    pref = evaluate("RULE-L1-TAKEOUT-DOUBLE-NT-OVER-MINOR", {"stopper": True, "minor_option": True})
    assert nt.action == "1NT"
    assert pref.action == "PREFER_NT_OVER_MINOR"


def test_cuebid_branch():
    assert evaluate("RULE-L1-DOUBLE-CUEBID-GAME-FORCE", {"HCP": 12, "has_5plus_major": False, "stopper": False}).action == "CUEBID;FG"


def test_forced_position():
    assert evaluate("RULE-L1-TAKEOUT-DOUBLE-FORCED-RESPONSE", {"partner_doubled_opening": True, "RHO_passed": True, "HCP": 2}).action == "PASS_FORBIDDEN"


def test_free_position():
    assert evaluate("RULE-L1-TAKEOUT-DOUBLE-FREE-POSITION", {"partner_doubled_opening": True, "RHO_made_meaningful_bid": True, "weak_unsuitable_hand": True}).action == "PASS_ALLOWED"


def test_nt_overcall():
    assert evaluate("RULE-L1-NT-OVERCALL-15-17-STOPPER", {"opponent_opening_level": 1, "HCP": 16, "balanced": True, "stopper": True}).action == "1NT"


def test_weak2_response_pass():
    assert evaluate("RULE-L1-WEAK2-RESPONSE-BANDS", {"HCP": 14}).action == "PASS"


def test_weak2_response_invite():
    assert evaluate("RULE-L1-WEAK2-RESPONSE-BANDS", {"HCP": 16}).action == "INVITE"


def test_weak3_fit_game():
    assert evaluate("RULE-L1-WEAK3-RESPONSE-BRANCHES", {"HCP": 15, "fit": True}).action == "GAME"


def test_distribution_long_hand():
    ctx = {"trump_fit": True, "combined_trumps": 9, "hand_role": "long_trump", "singleton": 1}
    assert evaluate("RULE-L1-DISTRIBUTION-POINTS-AFTER-FIT", ctx).action == 2


def test_distribution_short_hand_void():
    ctx = {"trump_fit": True, "combined_trumps": 8, "hand_role": "short_trump", "void": 1}
    assert evaluate("RULE-L1-DISTRIBUTION-POINTS-AFTER-FIT", ctx).action == 5


def test_scoring_4h_game():
    score = evaluate("RULE-L1-SCORING-UNDOUBLED-CONTRACT", {"level": 4, "strain": "H"}).action
    cls = evaluate("RULE-L1-SCORING-GAME-SLAM-CLASS", {"level": 4, "contract_trick_score": score}).action
    assert score == 120 and cls == "GAME"


def test_game_bonus_nonvul():
    assert evaluate("RULE-L1-SCORING-VUL-BONUS-UNDERTRICKS", {"class": "GAME", "vulnerable": False}).action == 300


def test_undertricks_vul():
    assert evaluate("RULE-L1-SCORING-VUL-BONUS-UNDERTRICKS", {"undertricks": 2, "vulnerable": True}).action == 200


def test_nt_plan_order():
    assert evaluate("RULE-L1-PLAY-NT-COUNT-TOP-TRICKS", {"top_tricks": 7}).action == 7
    assert evaluate("RULE-L1-PLAY-NT-FIND-EXTRA-TRICKS", {"top_tricks": 7, "required_tricks": 9}).action == 2


def test_trump_allowed_losers():
    assert evaluate("RULE-L1-PLAY-TRUMP-COUNT-LOSERS", {"contract_level": 4}).action == 3


def test_preserve_stopper():
    ctx = {"defense_long_suit_threat": True, "stopper_identified": True}
    assert evaluate("RULE-L1-PLAY-NT-PRESERVE-STOPPERS", ctx).action == "PRESERVE_STOPPER_UNTIL_NEEDED"


def test_partial_cannot_auto_promote():
    result = evaluate("RULE-L1-MACHINE-COMPILER-GATE", {"skill_status": "PARTIAL_CANON_SCOPE"})
    assert result.status == "BLOCK" and result.action == "AUTO_PROMOTION_FORBIDDEN"


def test_defense_signal_is_blocked():
    result = evaluate("RULE-L1-DEFENSE-SIGNAL-GATE", {"derive_signals_from_vague_standard_phrase": True})
    assert result.status == "BLOCK" and result.action == "BLOCKED_PENDING_TEACHER"


def test_runtime_conflict_gate():
    a = type(evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 4}))("A", "MATCH", "1H", 100, 5, 5)
    b = type(a)("B", "MATCH", "1S", 100, 5, 5)
    result = resolve([a, b])
    assert result.status == "BLOCK" and result.action == "RULE_CONFLICT"


def test_system_isolation():
    result = evaluate("RULE-L1-OPEN-1H", {"HCP": 13, "H": 5, "S": 4}, system_version="SCHOOL_TOURNAMENT_CURRENT_V1")
    assert result.status == "NO_MATCH"


def test_blocked_skill_cannot_promote():
    result = evaluate("RULE-L1-MACHINE-COMPILER-GATE", {"skill_status": "BLOCKED_PENDING_TEACHER"})
    assert result.status == "BLOCK"
