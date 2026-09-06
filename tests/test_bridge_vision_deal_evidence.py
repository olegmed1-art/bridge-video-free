from __future__ import annotations

import copy

import pytest

from bridge_vision.deal_evidence import (
    DEAL_EVIDENCE_SCHEMA,
    DealEvidenceError,
    build_deal_evidence_report,
    render_deal_diagram_markdown,
)


VERSION = "test-recognizer-v1"
FRAMES = ("a" * 64, "b" * 64)
SUITS = "HCDS"
RANKS = "AKQJT98765432"


def observation(
    seat: str,
    card: str,
    *,
    frame: str = FRAMES[0],
    timestamp_ms: int = 1000,
    index: int = 0,
    confidence: float = 0.91,
) -> dict:
    return {
        "seat": seat,
        "card": card,
        "source": "VISUAL",
        "frame_sha256": frame,
        "timestamp_ms": timestamp_ms,
        "region": {
            "coordinate_space": "NORMALIZED_FRAME",
            "x": round(0.02 + (index % 13) * 0.05, 6),
            "y": {"N": 0.05, "E": 0.28, "S": 0.51, "W": 0.74}[seat],
            "width": 0.04,
            "height": 0.04,
        },
        "confidence": confidence,
        "confidence_kind": "TEST_SCORE",
        "recognizer_version": VERSION,
    }


def complete_observations(*, frames: tuple[str, ...] = FRAMES) -> list[dict]:
    result = []
    for seat, suit in zip("NESW", SUITS):
        for index, rank in enumerate(RANKS):
            for frame_index, frame in enumerate(frames):
                result.append(
                    observation(
                        seat,
                        rank + suit,
                        frame=frame,
                        timestamp_ms=1000 + frame_index * 1000,
                        index=index,
                        confidence=0.93 - frame_index * 0.01,
                    )
                )
    return result


def pointer_for(item: dict, **changes) -> dict:
    region = item["region"]
    event = {
        "source": "TEACHER_POINTER",
        "frame_sha256": item["frame_sha256"],
        "timestamp_ms": item["timestamp_ms"],
        "point": {
            "coordinate_space": "NORMALIZED_FRAME",
            "x": region["x"] + region["width"] / 2,
            "y": region["y"] + region["height"] / 2,
        },
        "confidence": 0.95,
        "claimed_card": item["card"],
        "claimed_seat": item["seat"],
    }
    event.update(changes)
    return event


def test_complete_multiframe_deal_has_card_level_temporal_provenance():
    report = build_deal_evidence_report(
        complete_observations(), recognizer_version=VERSION
    )

    assert report["schema"] == DEAL_EVIDENCE_SCHEMA
    assert report["status"] == "COMPLETE_VISUAL"
    assert report["suit_order"] == ["H", "C", "D", "S"]
    assert report["integrity"] == {
        "observed_cards": 52,
        "inferred_cards": 0,
        "unknown_slots": 0,
        "unique_known_cards": 52,
        "total_seat_slots": 52,
        "observed_seat_counts": {seat: 13 for seat in "NESW"},
        "known_seat_counts": {seat: 13 for seat in "NESW"},
        "complete_supporting_frame_sha256s": list(FRAMES),
        "global_temporal_support": True,
    }
    assert len(report["card_records"]) == 52
    ace_hearts = next(
        item
        for item in report["card_records"]
        if item["seat"] == "N" and item["rank"] == "A" and item["suit"] == "H"
    )
    assert ace_hearts["source"] == "TEMPORAL_CONSENSUS"
    assert ace_hearts["confidence"] == 0.92
    assert [item["frame_sha256"] for item in ace_hearts["evidence"]] == list(FRAMES)
    assert ace_hearts["region"]["coordinate_space"] == "NORMALIZED_FRAME"
    assert report["canonical_promotion_allowed"] is False


def test_non_iterable_evidence_inputs_fail_closed():
    with pytest.raises(DealEvidenceError, match="visual_observations"):
        build_deal_evidence_report(None, recognizer_version=VERSION)
    with pytest.raises(DealEvidenceError, match="teacher_pointer_events"):
        build_deal_evidence_report([], None, recognizer_version=VERSION)


def test_one_frame_is_visual_but_not_temporal_consensus():
    report = build_deal_evidence_report(
        complete_observations(frames=(FRAMES[0],)), recognizer_version=VERSION
    )

    assert report["status"] == "PENDING_TEMPORAL_CONSENSUS"
    assert {item["source"] for item in report["card_records"]} == {"VISUAL"}


def test_per_card_support_cannot_form_a_hybrid_temporal_deal():
    frames = ("a" * 64, "b" * 64, "c" * 64)
    visual = []
    for card_index, item in enumerate(complete_observations(frames=frames)):
        frame_index = frames.index(item["frame_sha256"])
        if frame_index != card_index // len(frames) % len(frames):
            visual.append(item)
    report = build_deal_evidence_report(visual, recognizer_version=VERSION)

    assert report["integrity"]["observed_cards"] == 52
    assert {item["source"] for item in report["card_records"]} == {"TEMPORAL_CONSENSUS"}
    assert report["integrity"]["complete_supporting_frame_sha256s"] == []
    assert report["integrity"]["global_temporal_support"] is False
    assert report["status"] == "PENDING_TEMPORAL_CONSENSUS"


def test_pointer_corroborates_visual_card_but_never_becomes_observation():
    visual = observation("N", "AH")
    report = build_deal_evidence_report(
        [visual], [pointer_for(visual)], recognizer_version=VERSION
    )

    assert report["status"] == "PARTIAL"
    assert report["pointer_evidence"][0]["resolution"] == "CORROBORATES_VISUAL"
    record = next(item for item in report["card_records"] if item["rank"] == "A")
    assert record["source"] == "VISUAL"
    pointer = record["evidence"][-1]
    assert pointer["source"] == "TEACHER_POINTER"
    assert pointer["accepted_as_visual_observation"] is False


def test_pointer_visual_conflict_is_needs_review():
    visual = observation("N", "AH")
    report = build_deal_evidence_report(
        [visual],
        [pointer_for(visual, claimed_card="KH")],
        recognizer_version=VERSION,
    )

    assert report["status"] == "NEEDS_REVIEW"
    assert report["conflicts"][0]["type"] == "TEACHER_POINTER_VISUAL_CONFLICT"
    assert report["pointer_evidence"][0]["resolution"] == "CONFLICTS_WITH_VISUAL"


def test_pointer_on_changed_or_unrecognized_frame_is_needs_review():
    visual = observation("N", "AH")
    pointer = pointer_for(visual, frame_sha256="c" * 64)
    report = build_deal_evidence_report([visual], [pointer], recognizer_version=VERSION)

    assert report["status"] == "NEEDS_REVIEW"
    assert report["review_reasons"] == ["teacher_pointer_without_visual_target"]
    assert report["pointer_evidence"][0]["resolution"] == "NO_VISUAL_TARGET_AT_POINTER"

    timestamp_mismatch = pointer_for(visual, timestamp_ms=1001)
    report = build_deal_evidence_report(
        [visual], [timestamp_mismatch], recognizer_version=VERSION
    )
    assert report["status"] == "NEEDS_REVIEW"
    assert report["review_reasons"] == ["teacher_pointer_timestamp_mismatch"]
    assert (
        report["pointer_evidence"][0]["resolution"]
        == "POINTER_TIMESTAMP_DOES_NOT_MATCH_FRAME"
    )


def test_three_complete_visible_hands_remain_unknown_without_fourth_hand_inference():
    visual = []
    for seat, suit in zip("NES", "HCD"):
        for index, rank in enumerate(RANKS):
            for frame_index, frame in enumerate(FRAMES):
                visual.append(
                    observation(
                        seat,
                        rank + suit,
                        frame=frame,
                        timestamp_ms=1000 + 1000 * frame_index,
                        index=index,
                    )
                )
    report = build_deal_evidence_report(
        visual,
        recognizer_version=VERSION,
    )

    assert report["status"] == "PARTIAL"
    assert report["canonical_observed_deal"]["hands"]["W"]["unknown_count"] == 13
    assert report["integrity"]["observed_cards"] == 39
    assert report["integrity"]["inferred_cards"] == 0
    assert report["integrity"]["unknown_slots"] == 13
    assert not any(
        item["source"] == "LOGICAL_INFERENCE" for item in report["card_records"]
    )


def test_explicit_legacy_inference_switch_fails_closed():
    with pytest.raises(DealEvidenceError, match="fourth-hand inference is prohibited"):
        build_deal_evidence_report(
            [observation("N", "AH")],
            recognizer_version=VERSION,
            allow_logical_inference=True,
        )


def test_same_card_in_two_seats_is_not_silently_resolved():
    report = build_deal_evidence_report(
        [observation("N", "AH"), observation("E", "AH", index=1)],
        recognizer_version=VERSION,
    )

    assert report["status"] == "NEEDS_REVIEW"
    assert report["conflicts"][0]["type"] == "VISUAL_CARD_SEAT_CONFLICT"
    assert report["integrity"]["observed_cards"] == 0


def test_more_than_thirteen_cards_in_a_hand_is_needs_review():
    cards = [rank + suit for suit in SUITS for rank in RANKS][:14]
    visual = [observation("N", card, index=index) for index, card in enumerate(cards)]
    report = build_deal_evidence_report(visual, recognizer_version=VERSION)

    assert report["status"] == "NEEDS_REVIEW"
    assert any(item["type"] == "HAND_EXCEEDS_13_CARDS" for item in report["conflicts"])
    assert report["canonical_observed_deal"] is None


def test_repeated_run_and_input_order_are_deterministic():
    visual = complete_observations()
    first = build_deal_evidence_report(visual, recognizer_version=VERSION)
    second = build_deal_evidence_report(
        list(reversed(copy.deepcopy(visual))), recognizer_version=VERSION
    )
    assert first == second


def test_diagram_uses_required_suit_order_and_marks_provenance():
    visual = [
        observation("N", "AH", index=0),
        observation("N", "KC", index=1),
        observation("N", "QD", index=2),
        observation("N", "JS", index=3),
    ]
    report = build_deal_evidence_report(visual, recognizer_version=VERSION)
    rendered = render_deal_diagram_markdown(report)

    assert rendered.splitlines()[0] == "| Seat | ♥ H | ♣ C | ♦ D | ♠ S | Unknown |"
    assert "| N | A[V] | K[V] | Q[V] | J[V] | 9 |" in rendered


def test_regions_are_normalized_and_cannot_leave_the_frame():
    visual = observation("N", "AH")
    visual["region"] = {
        "coordinate_space": "NORMALIZED_FRAME",
        "x": 0.99,
        "y": 0.1,
        "width": 0.02,
        "height": 0.1,
    }
    with pytest.raises(DealEvidenceError, match="leaves normalized frame"):
        build_deal_evidence_report([visual], recognizer_version=VERSION)
