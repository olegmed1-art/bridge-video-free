import pytest

from bridge_school_api.tournament_scoring_context_29912_v3 import (
    Tournament29912ScoringError,
    derive_session_score_context,
)


def _boards():
    return [
        {"board": 1, "pair_matchpoints": 5.0, "source_consistency": {"ok": True}},
        {"board": 2, "pair_matchpoints": -2.0, "source_consistency": {"ok": True}},
    ]


def test_signed_score_contributions_are_preserved_and_additive():
    evidence, outcomes = derive_session_score_context(
        round_no=1,
        tournament={"session_score": 5.0},
        boards=_boards(),
        skipped_rows=[{"board": 3, "row": ["3", "", "", "", "2"]}],
    )
    assert evidence.verified is True
    assert evidence.analyzed_board_sum == 3.0
    assert evidence.skipped_numeric_sum == 2.0
    assert evidence.known_source_sum == 5.0
    assert evidence.unexplained_remainder == 0.0
    assert [x.source_pair_score_contribution for x in outcomes] == [5.0, -2.0]
    assert [x.negative_score_contribution for x in outcomes] == [0.0, 2.0]


def test_unexplained_session_remainder_is_retained_not_guessed():
    evidence, _ = derive_session_score_context(
        round_no=5,
        tournament={"session_score": 6.0},
        boards=_boards(),
    )
    assert evidence.verified is False
    assert evidence.known_source_sum == 3.0
    assert evidence.unexplained_remainder == 3.0
    assert evidence.absolute_difference == 3.0


def test_non_numeric_board_contribution_fails_closed():
    bad = _boards()
    bad[0] = {**bad[0], "pair_matchpoints": "adjusted"}
    with pytest.raises(Tournament29912ScoringError, match="must be numeric"):
        derive_session_score_context(
            round_no=1,
            tournament={"session_score": 0.0},
            boards=bad,
        )


def test_boolean_round_is_rejected():
    with pytest.raises(Tournament29912ScoringError, match="positive integer"):
        derive_session_score_context(
            round_no=True,
            tournament={"session_score": 0.0},
            boards=_boards(),
        )
