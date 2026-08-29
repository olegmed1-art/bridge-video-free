from bridge_vision.shadow_pbn import render_shadow_pbn
from bridge_vision.transcript_card_observer import (
    extract_russian_card_mentions,
    observe_transcript_cards,
)


def frame_record(*, card="KH", seat="S", time=240.0, layout=False):
    evidence = {
        "canonical_promotion_allowed": False,
        "deal_identity": {"kind": "EXPLICIT_BOARD", "scope": "diana-14", "value": "board-1"},
        "board_metadata": {
            "status": "CONFIRMED",
            "board_number": 1,
            "dealer": "N",
            "vulnerability": "NONE",
            "seat_positions": {"top": "N", "right": "E", "bottom": "S", "left": "W"},
        },
    }
    hands = {}
    if layout:
        evidence["layout_suggestions"] = [{
            "index": 7,
            "seat": seat,
            "provenance_class": "LAYOUT_SUGGESTION",
            "resolution": "LAYOUT_UNIQUE_SUGGESTION",
            "suggested_card": card,
            "pointer_corroboration": {
                "source": "VISUAL_POINTER",
                "confidence": 0.96,
                "evidence_locator": "frame-002.jpg#pointer=0",
                "accepted_as_card_observation": False,
            },
            "accepted_as_observation": False,
        }]
    else:
        hands = {seat: [card]}
    return {
        "status": "PARTIAL_BOARD_OBSERVATION",
        "time": time,
        "frame_file": "frame-002.jpg",
        "frame_sha256": "a" * 64,
        "candidates": [{"hands": hands, "confidence": 0.98, "evidence": evidence}],
        "diagnostics": [],
    }


def row(text, *, start=201.0, end=208.0, role="TEACHER", verified=False):
    return {
        "start": start,
        "end": end,
        "text": text,
        "speaker": "speaker-0",
        "speaker_role_candidate": role,
        "speaker_confidence": 0.94,
        "speaker_identity_verified": verified,
        "speaker_role_verified": verified,
    }


def test_exact_russian_rank_and_suit_are_normalized_and_auction_context_is_rejected():
    mentions = extract_russian_card_mentions([
        row("Я вижу короля червей у тебя."),
        row("Он открылся двумя трефами, это заявка."),
        row("Если будет туз пик, то возьмем взятку."),
    ])
    assert [(item["card"], item["extraction_status"], item["reason"]) for item in mentions] == [
        ("KH", "EXACT_AFFIRMATIVE", None),
        ("2C", "REVIEW", "AUCTION_CONTEXT"),
        ("AS", "REVIEW", "HYPOTHETICAL_CARD_MENTION"),
    ]


def test_student_speech_only_corroborates_layout_and_cannot_promote_a_card():
    result = observe_transcript_cards(
        [row("У тебя есть король червей.", role="STUDENT")],
        [frame_record(layout=True)],
    )
    assert result["status"] == "REVIEW"
    observation = result["observations"][0]
    assert observation["card"] == "KH"
    assert observation["reason"] == "LAYOUT_PROMOTION_REQUIRES_VERIFIED_TEACHER_AND_POINTER"
    assert observation["accepted_as_observation"] is False
    assert observation["canonical_promotion_allowed"] is False


def test_verified_teacher_speech_plus_pointer_and_layout_becomes_shadow_observation():
    result = observe_transcript_cards(
        [row("У тебя есть король червей.", verified=True)],
        [frame_record(layout=True)],
    )
    assert result["status"] == "PASS"
    observation = result["observations"][0]
    assert observation["seat"] == "S"
    assert observation["card"] == "KH"
    assert observation["provenance_class"] == "OBSERVED_MULTIMODAL"
    assert observation["accepted_as_observation"] is True


def test_speech_corroborates_an_already_accepted_visual_card_without_reassigning_it():
    result = observe_transcript_cards(
        [row("Я вижу короля червей на юге.")],
        [frame_record()],
    )
    observation = result["observations"][0]
    assert observation["resolution"] == "CORROBORATES_ACCEPTED_VISUAL"
    assert observation["provenance_class"] == "OBSERVED_VISUAL_WITH_SPEECH_CORROBORATION"
    assert observation["seat"] == "S"


def test_speech_visual_seat_disagreement_fails_closed():
    result = observe_transcript_cards(
        [row("У партнера есть король червей.")],
        [frame_record()],
    )
    observation = result["observations"][0]
    assert result["status"] == "CONFLICT"
    assert observation["reason"] == "SPEECH_VISUAL_SEAT_DISAGREEMENT"
    assert observation["accepted_as_observation"] is False


def test_equal_distance_frames_are_not_guessed():
    result = observe_transcript_cards(
        [row("Я вижу короля червей.", start=115, end=125)],
        [frame_record(time=60), frame_record(time=180)],
    )
    assert result["observations"][0]["reason"] == "AMBIGUOUS_NEAREST_FRAME"


def test_multimodal_observation_is_saved_in_partial_shadow_pbn():
    record = frame_record(layout=True)
    observation = observe_transcript_cards(
        [row("У тебя есть король червей.", verified=True)],
        [record],
    )["observations"][0]
    record["transcript_card_observations"] = [observation]
    text = render_shadow_pbn([record], source="Diana 14")
    assert '[X-Observed-S "-.K.-.-"]' in text
    assert '[X-ObservedCount "1"]' in text
    assert '[Deal "' not in text
