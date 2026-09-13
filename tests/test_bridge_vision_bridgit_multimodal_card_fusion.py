from __future__ import annotations

import copy

from bridge_vision.bridgit_card_evidence_fusion import fuse_card_evidence
from bridge_vision.bridgit_suit_color import classify_suit_color
from bridge_vision.bridgit_teacher_speech import accumulate_teacher_speech


def slot(
    slot_id: str,
    seat: str,
    timestamp_ms: int,
    candidates: list[tuple[str, float]],
) -> dict:
    return {
        "slot_id": slot_id,
        "seat": seat,
        "timestamp_ms": timestamp_ms,
        "frame_sha256": (slot_id.encode().hex() + "0" * 64)[:64],
        "candidates": [
            {"card": card, "score": score} for card, score in candidates
        ],
    }


def segment(
    event_id: str,
    start_ms: int,
    text: str,
    confidence: float = 0.98,
    **extra,
) -> dict:
    return {
        "event_id": event_id,
        "speaker_role": "TEACHER",
        "start_ms": start_ms,
        "end_ms": start_ms + 500,
        "text": text,
        "asr_confidence": confidence,
        **extra,
    }


def test_teacher_context_combines_partial_mentions_and_repetition() -> None:
    result = accumulate_teacher_speech(
        [
            segment("e1", 0, "Посмотрим руку Севера"),
            segment("e2", 2_000, "здесь дама", 0.95),
            segment("e3", 5_000, "пиковая дама"),
            segment("e4", 8_000, "эта дама пик"),
        ]
    )
    active = [claim for claim in result["claims"] if not claim["retracted"]]
    assert len(active) == 1
    assert active[0]["seat"] == "N"
    assert active[0]["card"] == "QS"
    assert active[0]["speech_confidence"] > 0.95
    assert result["mouse_cursor_used"] is False


def test_teacher_can_build_full_card_from_rank_then_suit() -> None:
    result = accumulate_teacher_speech(
        [
            segment("e1", 0, "у Востока король"),
            segment("e2", 3_000, "в черве"),
        ]
    )
    active = [claim for claim in result["claims"] if not claim["retracted"]]
    assert len(active) == 1
    assert active[0]["seat"] == "E"
    assert active[0]["card"] == "KH"


def test_teacher_correction_retracts_previous_claim() -> None:
    result = accumulate_teacher_speech(
        [
            segment("e1", 0, "у Юга король пик", 0.95),
            segment("e2", 2_000, "нет, дама пик", 0.99),
        ]
    )
    assert result["claims"][0]["card"] == "KS"
    assert result["claims"][0]["retracted"] is True
    assert result["claims"][0]["retracted_by"] == "e2"
    assert result["claims"][1]["card"] == "QS"
    assert result["claims"][1]["retracted"] is False


def test_overlapping_duplicate_asr_does_not_multiply_support() -> None:
    one = accumulate_teacher_speech(
        [segment("a", 2_000, "у Севера дама пик", 0.90)]
    )
    duplicated = accumulate_teacher_speech(
        [
            segment("a", 2_000, "у Севера дама пик", 0.90),
            segment("b", 2_400, "у Севера дама пик", 0.80),
        ]
    )
    assert (
        duplicated["claims"][0]["speech_confidence"]
        == one["claims"][0]["speech_confidence"]
    )
    assert len(duplicated["segments"]) == 1


def test_overlapping_expanding_asr_does_not_multiply_support() -> None:
    result = accumulate_teacher_speech(
        [
            segment("a", 2_000, "у Севера дама", 0.90),
            segment("b", 2_400, "у Севера дама пик", 0.80),
        ]
    )
    assert len(result["segments"]) == 1
    assert result["claims"][0]["card"] == "QS"
    assert result["claims"][0]["speech_confidence"] == 0.80


def test_plain_negation_does_not_create_positive_card_claim() -> None:
    result = accumulate_teacher_speech(
        [
            segment("seat", 0, "Посмотрим руку Севера"),
            segment("negative", 2_000, "здесь нет дамы пик", 0.99),
        ]
    )
    assert result["claims"] == []
    assert result["segments"][1]["is_negated"] is True


def test_apostrophes_do_not_create_one_letter_seat_or_suit_aliases() -> None:
    possessive = accumulate_teacher_speech(
        [segment("possessive", 0, "South's king", 0.99)]
    )
    contraction = accumulate_teacher_speech(
        [segment("contraction", 0, "it's a queen", 0.99)]
    )
    assert possessive["claims"][0]["seat"] is None
    assert possessive["claims"][0]["suit"] is None
    assert possessive["claims"][0]["card"] is None
    assert contraction["claims"] == []


def test_partial_rank_and_suit_confidence_stays_conjunctive() -> None:
    speech = [
        segment("rank", 0, "у Севера дама", 0.90),
        segment("suit", 2_000, "пик", 0.90),
    ]
    parsed = accumulate_teacher_speech(speech)
    assert parsed["claims"][0]["card"] == "QS"
    assert parsed["claims"][0]["speech_confidence"] < 0.78
    fused = fuse_card_evidence(
        [slot("slot-q", "N", 2_000, [("JS", 0.48), ("QS", 0.47)])],
        teacher_speech=speech,
    )
    assert fused["decisions"][0]["card"] != "QS"


def test_decision_fusion_uses_asymmetric_speech_window() -> None:
    speech = [segment("claim", 20_000, "у Севера дама пик", 0.99)]
    before = fuse_card_evidence(
        [slot("before", "N", 7_000, [("JS", 0.48), ("QS", 0.47)])],
        teacher_speech=speech,
    )
    after = fuse_card_evidence(
        [slot("after", "N", 26_000, [("JS", 0.48), ("QS", 0.47)])],
        teacher_speech=speech,
    )
    assert before["decisions"][0]["card"] != "QS"
    assert after["decisions"][0]["card"] != "QS"
    assert before["decisions"][0]["speech_claim_ids"] == []
    assert after["decisions"][0]["speech_claim_ids"] == []


def test_suit_colour_uses_profile_relative_palette() -> None:
    red = classify_suit_color(
        [(205, 30, 30), (210, 35, 35), (250, 250, 250)],
        red_reference=(205, 30, 30),
        black_reference=(30, 30, 30),
        background_reference=(250, 250, 250),
    )
    black = classify_suit_color(
        [(28, 28, 28), (35, 34, 33), (250, 250, 250)],
        red_reference=(205, 30, 30),
        black_reference=(30, 30, 30),
        background_reference=(250, 250, 250),
    )
    assert red["family"] == "RED"
    assert black["family"] == "BLACK"
    assert red["calibrated"] is True
    assert black["calibrated"] is True


def test_colour_can_resolve_close_red_black_visual_tie() -> None:
    result = fuse_card_evidence(
        [slot("slot-a", "N", 1_000, [("AH", 0.51), ("AS", 0.50)])],
        color_evidence=[
            {
                "slot_id": "slot-a",
                "family": "BLACK",
                "confidence": 0.95,
                "calibrated": True,
            }
        ],
    )
    decision = result["decisions"][0]
    assert decision["card"] == "AS"
    assert decision["provenance"] == "VISUAL_PLUS_SUIT_COLOR"


def test_repeated_teacher_card_can_resolve_weak_visual_tie() -> None:
    speech = [
        segment("e1", 0, "Посмотрим руку Севера"),
        segment("e2", 2_000, "здесь дама", 0.95),
        segment("e3", 5_000, "пиковая дама"),
        segment("e4", 8_000, "эта дама пик"),
    ]
    result = fuse_card_evidence(
        [
            slot(
                "slot-q",
                "N",
                8_000,
                [("JS", 0.48), ("QS", 0.47), ("QH", 0.30)],
            )
        ],
        teacher_speech=speech,
    )
    decision = result["decisions"][0]
    assert decision["card"] == "QS"
    assert decision["provenance"] == "VISUAL_PLUS_TEACHER_SPEECH"
    assert decision["baseline_visual_card"] == "JS"


def test_strong_visual_evidence_beats_repeated_teacher_misspeak() -> None:
    speech = [
        segment("e1", 1_000, "у Севера король пик"),
        segment("e2", 4_000, "король пик"),
    ]
    result = fuse_card_evidence(
        [slot("slot-a", "N", 4_000, [("AS", 0.95), ("KS", 0.30)])],
        teacher_speech=speech,
    )
    decision = result["decisions"][0]
    assert decision["card"] == "AS"
    assert decision["status"] == "ACCEPTED_STRONG_VISUAL"
    assert {item["kind"] for item in decision["conflicts"]} == {
        "SPEECH_VISUAL_CONFLICT"
    }


def test_speech_cannot_create_card_absent_from_visual_candidates() -> None:
    speech = [
        segment("e1", 0, "у Севера дама пик"),
        segment("e2", 3_000, "дама пик"),
        segment("e3", 6_000, "дама пик"),
    ]
    result = fuse_card_evidence(
        [slot("slot-j", "N", 6_000, [("JS", 0.49), ("TS", 0.47)])],
        teacher_speech=speech,
    )
    assert result["decisions"][0]["card"] != "QS"


def test_ambiguous_speech_binding_does_not_guess_a_slot() -> None:
    speech = [segment("e1", 2_000, "у Севера дама пик")]
    result = fuse_card_evidence(
        [
            slot("slot-1", "N", 2_000, [("QS", 0.40), ("JS", 0.39)]),
            slot("slot-2", "N", 2_100, [("QS", 0.35), ("TS", 0.34)]),
        ],
        teacher_speech=speech,
    )
    assert any(
        conflict["kind"] == "AMBIGUOUS_SPEECH_BINDING"
        for conflict in result["conflicts"]
    )
    assert all(
        not decision["speech_claim_ids"] for decision in result["decisions"]
    )


def test_mouse_cursor_fields_are_ignored() -> None:
    base = segment("e1", 2_000, "у Севера дама пик")
    with_cursor = copy.deepcopy(base)
    with_cursor["cursor_x"] = 0.71
    with_cursor["cursor_y"] = 0.18
    visual = [slot("slot-q", "N", 2_000, [("QS", 0.46), ("JS", 0.45)])]
    first = fuse_card_evidence(visual, teacher_speech=[base])
    second = fuse_card_evidence(visual, teacher_speech=[with_cursor])
    assert first["decisions"] == second["decisions"]
    assert first["mouse_cursor_used"] is False
    assert second["mouse_cursor_used"] is False


def test_fusion_never_uses_hidden_hand_or_deck_complement() -> None:
    result = fuse_card_evidence(
        [slot("slot-a", "N", 1_000, [("AS", 0.91), ("KS", 0.20)])]
    )
    assert result["hidden_hand_inference_used"] is False
    assert result["deck_complement_used"] is False
    assert result["canonical_promotion_allowed"] is False
