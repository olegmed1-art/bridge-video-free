import pytest

from bridge_vision.temporal_visibility import (
    AMBIGUOUS,
    NOT_EXPECTED,
    OCCLUDED,
    PLAYED,
    TemporalCardVisibilityTracker,
    TemporalVisibilityError,
    VISIBLE,
    VISIBLE_FN,
)


def statuses(result):
    return {(row["seat"], row["card"]): row["status"] for row in result["cards"]}


def test_verified_played_card_is_not_counted_as_visible_false_negative():
    tracker = TemporalCardVisibilityTracker()
    first = tracker.observe(
        deal_key="board-7",
        frame_id="frame-1",
        visible_hands={"S": ["AS", "KS", "QS"]},
        expected_hands={"S": ["AS", "KS", "QS"]},
    )
    assert first["counts"][VISIBLE] == 3

    second = tracker.observe(
        deal_key="board-7",
        frame_id="frame-2",
        visible_hands={"S": ["KS", "QS"]},
        expected_hands={"S": ["AS", "KS", "QS"]},
        play_events=[{
            "seat": "S",
            "card": "AS",
            "verified": True,
            "evidence_locator": "frame-2#center-play",
        }],
    )
    assert statuses(second)[("S", "AS")] == PLAYED
    assert second["counts"][VISIBLE_FN] == 0


def test_disappearance_alone_remains_visible_false_negative():
    tracker = TemporalCardVisibilityTracker()
    tracker.observe(
        deal_key="board-1",
        frame_id="frame-1",
        visible_hands={"N": ["AH", "KH"]},
        expected_hands={"N": ["AH", "KH"]},
    )
    second = tracker.observe(
        deal_key="board-1",
        frame_id="frame-2",
        visible_hands={"N": ["KH"]},
        expected_hands={"N": ["AH", "KH"]},
    )
    assert statuses(second)[("N", "AH")] == VISIBLE_FN


def test_occluded_ambiguous_and_not_expected_are_separate_states():
    tracker = TemporalCardVisibilityTracker()
    result = tracker.observe(
        deal_key="board-2",
        frame_id="frame-1",
        visible_hands={"E": ["AC"]},
        expected_hands={"E": ["AC", "KC", "QC", "JC"]},
        occluded=[{"seat": "E", "card": "KC"}],
        ambiguous=[{"seat": "E", "card": "QC"}],
        not_expected_visible=[{"seat": "E", "card": "JC"}],
    )
    state = statuses(result)
    assert state[("E", "AC")] == VISIBLE
    assert state[("E", "KC")] == OCCLUDED
    assert state[("E", "QC")] == AMBIGUOUS
    assert state[("E", "JC")] == NOT_EXPECTED
    assert result["counts"][VISIBLE_FN] == 0


def test_unverified_or_unseen_play_event_fails_closed():
    tracker = TemporalCardVisibilityTracker()
    with pytest.raises(TemporalVisibilityError, match="explicitly verified"):
        tracker.observe(
            deal_key="board-3",
            frame_id="frame-1",
            visible_hands={"W": ["AD"]},
            play_events=[{"seat": "W", "card": "AD", "verified": False, "evidence_locator": "x"}],
        )

    with pytest.raises(TemporalVisibilityError, match="not previously observed"):
        tracker.observe(
            deal_key="board-4",
            frame_id="frame-1",
            visible_hands={},
            play_events=[{"seat": "W", "card": "AD", "verified": True, "evidence_locator": "x"}],
        )


def test_deals_and_duplicate_frames_do_not_cross_contaminate():
    tracker = TemporalCardVisibilityTracker()
    tracker.observe(deal_key="board-a", frame_id="same", visible_hands={"S": ["AS"]})
    tracker.observe(deal_key="board-b", frame_id="same", visible_hands={"S": ["AS"]})
    with pytest.raises(TemporalVisibilityError, match="duplicate frame"):
        tracker.observe(deal_key="board-a", frame_id="same", visible_hands={"S": ["AS"]})
