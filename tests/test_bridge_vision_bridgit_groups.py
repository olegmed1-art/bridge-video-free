import pytest

from bridge_vision.bridgit_groups import group_card_starts


def test_realistic_overlap_pitch_forms_separate_runs_without_naming_suits():
    assert group_card_starts([317, 346, 375, 404, 492, 527, 556, 644, 679, 767, 802]) == [
        [317, 346, 375, 404], [492], [527, 556], [644], [679], [767], [802]
    ]


def test_close_duplicate_like_edges_fail_closed():
    with pytest.raises(ValueError, match="implausibly small gap"):
        group_card_starts([317, 318, 346])


def test_unsorted_or_duplicate_starts_fail_closed():
    with pytest.raises(ValueError, match="unique and sorted"):
        group_card_starts([346, 317])
