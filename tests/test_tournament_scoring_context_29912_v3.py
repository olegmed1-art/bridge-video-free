import pytest

from bridge_school_api.tournament_scoring_context_29912_v3 import (
    Tournament29912ScoringError,
    derive_session_mp_context,
)


def _boards():
    return [
        {"board": 1, "pair_matchpoints": 4.0, "source_consistency": {"ok": True}},
        {"board": 2, "pair_matchpoints": 0.0, "source_consistency": {"ok": True}},
    ]


def test_mp_scale_requires_reproduction_of_reported_session_score():
    scale, outcomes = derive_session_mp_context(
        round_no=1,
        tournament={"field_size": 3, "session_score": 50.0},
        boards=_boards(),
    )
    assert scale.verified is True
    assert scale.max_matchpoints_per_board == 4.0
    assert scale.derived_session_percentage == 50.0
    assert [x.observed_pair_percentage for x in outcomes] == [100.0, 0.0]
    assert outcomes[1].gap_to_neutral_percentage_points == 50.0


def test_mp_scale_fails_closed_when_formula_does_not_match_source_score():
    with pytest.raises(Tournament29912ScoringError, match="does not reproduce"):
        derive_session_mp_context(
            round_no=1,
            tournament={"field_size": 3, "session_score": 60.0},
            boards=_boards(),
        )


def test_matchpoints_outside_verified_board_scale_are_rejected():
    bad = _boards()
    bad[0] = {**bad[0], "pair_matchpoints": 4.5}
    with pytest.raises(Tournament29912ScoringError, match="outside"):
        derive_session_mp_context(
            round_no=1,
            tournament={"field_size": 3, "session_score": 50.0},
            boards=bad,
        )


def test_boolean_field_size_is_rejected():
    with pytest.raises(Tournament29912ScoringError, match="positive integer"):
        derive_session_mp_context(
            round_no=1,
            tournament={"field_size": True, "session_score": 50.0},
            boards=_boards(),
        )
