from __future__ import annotations

import copy
import hashlib

from bridge_vision.bridgit_teacher_asr_adapter import (
    adapt_universal_video_teacher_segments,
)
from bridge_vision.bridgit_teacher_timeline_fusion import (
    fuse_teacher_speech_with_visual_timeline,
)


def transcript(
    text: str,
    start: float,
    end: float,
    *,
    role: str = "teacher",
    speaker_confidence: float = 0.96,
    role_confidence: float = 0.97,
    unreliable: bool = False,
) -> dict:
    return {
        "start": start,
        "end": end,
        "text": text,
        "unreliable": unreliable,
        "speaker_role_candidate": role,
        "speaker_confidence": speaker_confidence,
        "speaker_role_confidence": role_confidence,
    }


def visual_frame(index: int, timestamp_ms: int, cards: list[dict]) -> dict:
    materialized = []
    for offset, card in enumerate(cards):
        item = dict(card)
        item.setdefault("confidence", 0.92)
        item.setdefault(
            "evidence_pixel_sha256",
            hashlib.sha256(
                f"{index}|{offset}|{item['card']}|{item['seat']}|{item['source']}".encode()
            ).hexdigest(),
        )
        materialized.append(item)
    return {
        "frame_sha256": hashlib.sha256(f"frame-{index}".encode()).hexdigest(),
        "timestamp_ms": timestamp_ms,
        "cards": materialized,
    }


def test_adapter_preserves_source_video_clock_in_milliseconds() -> None:
    result = adapt_universal_video_teacher_segments(
        [transcript("у Юга дама пик", 12.345, 13.125)]
    )
    segment = result["segments"][0]
    assert segment["start_ms"] == 12345
    assert segment["end_ms"] == 13125
    assert result["source_clock"] == "SOURCE_VIDEO_TIMELINE"
    assert result["per_segment_asr_probability_claimed"] is False


def test_adapter_rejects_unreliable_or_non_teacher_segments() -> None:
    result = adapt_universal_video_teacher_segments(
        [
            transcript("дама пик", 1.0, 2.0, unreliable=True),
            transcript("дама пик", 3.0, 4.0, role="student"),
        ]
    )
    assert result["segments"] == []
    assert {item["reason"] for item in result["rejected"]} == {
        "ASR_SEGMENT_UNRELIABLE",
        "NOT_TEACHER_ROLE",
    }


def test_played_card_before_utterance_is_bound_to_its_owning_seat() -> None:
    frames = [
        visual_frame(
            1,
            10_000,
            [{"card": "QS", "seat": "S", "source": "PLAYED"}],
        )
    ]
    result = fuse_teacher_speech_with_visual_timeline(
        frames,
        [transcript("у Юга дама пик", 12.0, 13.0)],
    )
    assert len(result["corroborations"]) == 1
    item = result["corroborations"][0]
    assert item["card"] == "QS"
    assert item["seat"] == "S"
    assert item["ownership"] == "S"
    assert item["source"] == "PLAYED"
    assert item["provenance"] == "PLAYED_PLUS_TEACHER_SPEECH"
    assert item["temporal_distance_ms"] == 2_000


def test_hand_card_during_utterance_is_corroborated() -> None:
    frames = [
        visual_frame(
            2,
            5_500,
            [{"card": "KH", "seat": "E", "source": "HAND"}],
        )
    ]
    result = fuse_teacher_speech_with_visual_timeline(
        frames,
        [transcript("у Востока король червей", 5.0, 6.0)],
    )
    item = result["corroborations"][0]
    assert item["provenance"] == "HAND_PLUS_TEACHER_SPEECH"
    assert item["temporal_distance_ms"] == 0


def test_speech_without_matching_visual_card_does_not_create_observation() -> None:
    frames = [
        visual_frame(
            3,
            10_000,
            [{"card": "JS", "seat": "N", "source": "HAND"}],
        )
    ]
    result = fuse_teacher_speech_with_visual_timeline(
        frames,
        [transcript("у Севера дама пик", 10.0, 11.0)],
    )
    assert result["corroborations"] == []
    assert result["unresolved_speech_claims"][0]["card"] == "QS"
    assert result["hidden_hand_inference_used"] is False
    assert result["deck_complement_used"] is False


def test_same_card_at_wrong_seat_does_not_bind() -> None:
    frames = [
        visual_frame(
            4,
            10_000,
            [{"card": "QS", "seat": "E", "source": "PLAYED"}],
        )
    ]
    result = fuse_teacher_speech_with_visual_timeline(
        frames,
        [transcript("у Юга дама пик", 10.0, 11.0)],
    )
    assert result["corroborations"] == []
    assert result["unresolved_speech_claims"][0]["seat"] == "S"


def test_visual_card_too_far_from_utterance_does_not_bind() -> None:
    frames = [
        visual_frame(
            5,
            1_000,
            [{"card": "AS", "seat": "N", "source": "HAND"}],
        )
    ]
    result = fuse_teacher_speech_with_visual_timeline(
        frames,
        [transcript("у Севера туз пик", 30.0, 31.0)],
    )
    assert result["corroborations"] == []
    assert result["unresolved_speech_claims"][0]["reason"] == (
        "NO_MATCHING_VISUAL_CARD_IN_TEMPORAL_WINDOW"
    )


def test_duplicate_visual_pixels_do_not_multiply_speech_corroboration() -> None:
    first = visual_frame(
        6,
        8_000,
        [{"card": "QD", "seat": "N", "source": "HAND"}],
    )
    second = visual_frame(
        7,
        8_500,
        [{"card": "QD", "seat": "N", "source": "HAND"}],
    )
    second["cards"][0]["evidence_pixel_sha256"] = first["cards"][0][
        "evidence_pixel_sha256"
    ]
    result = fuse_teacher_speech_with_visual_timeline(
        [first, second],
        [transcript("у Севера дама бубен", 8.0, 9.0)],
    )
    assert result["corroborations"][0]["independent_visual_evidence_count"] == 1


def test_cursor_fields_have_no_effect_on_timeline_fusion() -> None:
    base = transcript("у Юга туз червей", 4.0, 5.0)
    cursor = copy.deepcopy(base)
    cursor["cursor_x"] = 0.8
    cursor["cursor_y"] = 0.2
    frames = [
        visual_frame(
            8,
            4_500,
            [{"card": "AH", "seat": "S", "source": "PLAYED"}],
        )
    ]
    first = fuse_teacher_speech_with_visual_timeline(frames, [base])
    second = fuse_teacher_speech_with_visual_timeline(frames, [cursor])
    assert first["corroborations"] == second["corroborations"]
    assert first["mouse_cursor_used"] is False
    assert second["mouse_cursor_used"] is False
