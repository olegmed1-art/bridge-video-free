import json
import random
from pathlib import Path

from bridge_vision.teacher_card_reconstruction_r265 import (
    extract_teacher_card_declarations,
    reconstruct_deal,
)

RANKS = "AKQJT98765432"
FULL_DECK = [rank + suit for suit in "SHDC" for rank in RANKS]


def teacher_segment(text, *, start=10.0, end=12.0, confidence=0.95, unreliable=False):
    return {
        "start": start,
        "end": end,
        "text": text,
        "speaker": "SPEAKER_A",
        "speaker_role_candidate": "teacher",
        "speaker_role_confidence": confidence,
        "unreliable": unreliable,
    }


def extract(text, **kwargs):
    result = extract_teacher_card_declarations([teacher_segment(text, **kwargs)])
    return result, result["declarations"]


def test_asr_text_extracts_exact_teacher_cards_and_single_seat():
    result, declarations = extract("У Запада король пик и двойка треф.")

    assert result["status"] == "EXTRACTED"
    assert {(row["card"], row["seat"]) for row in declarations} == {
        ("KS", "W"),
        ("2C", "W"),
    }
    assert all(row["source"] == "TEACHER_SPEECH" for row in declarations)
    assert result["asr_text_consumed"] is True
    assert result["visual_cards_checked_against_speech"] is False


def test_asr_rejects_unreliable_or_unproved_teacher_segments():
    unreliable, _ = extract("У Севера туз пик.", unreliable=True)
    low_role, _ = extract("У Севера туз пик.", confidence=0.72)

    assert unreliable["declarations"] == []
    assert unreliable["rejected"][0]["reason"] == "UNRELIABLE_ASR"
    assert low_role["declarations"] == []
    assert low_role["rejected"][0]["reason"] == "TEACHER_ROLE_NOT_PROVED"


def test_asr_rejects_card_statement_with_multiple_seats():
    result, _ = extract("Туз пик у Севера, а у Запада король пик.")

    assert result["declarations"] == []
    assert result["rejected"][0]["reason"] == "AMBIGUOUS_SEAT"


def test_visual_card_is_not_checked_or_conflicted_by_teacher_speech():
    _, declarations = extract("У Запада туз пик.")
    result = reconstruct_deal(
        {"N": ["AS"]},
        declarations,
        deal_timestamp_seconds=11.0,
    )

    assert result["status"] == "PARTIAL_WITH_EVIDENCE"
    assert result["deal"]["hands"]["N"]["cards"] == ["AS"]
    assert result["deal"]["hands"]["W"]["cards"] == []
    assert result["conflicts"] == []
    assert result["ignored_speech_cards"][0]["reason"] == "VISUAL_CARD_ALREADY_RESOLVED"
    assert result["visual_cards_checked_against_speech"] is False


def test_teacher_speech_fills_unknown_card_in_any_hand_then_complements_fourth():
    _, declarations = extract("У Севера двойка пик.")
    result = reconstruct_deal(
        {
            "N": [f"{rank}S" for rank in RANKS[:-1]],
            "E": [f"{rank}H" for rank in RANKS],
            "S": [f"{rank}D" for rank in RANKS],
            "W": [],
        },
        declarations,
        deal_timestamp_seconds=11.0,
    )

    assert result["status"] == "RECONSTRUCTED_FULL"
    assert result["seat_counts"] == {"N": 13, "E": 13, "S": 13, "W": 13}
    assert result["unique_cards"] == 52
    assert result["deal"]["card_provenance"]["N"]["TEACHER_SPEECH"] == ["2S"]
    assert len(result["deal"]["card_provenance"]["W"]["INFERRED_DECK_COMPLEMENT"]) == 13


def test_partial_fourth_hand_is_completed_when_consistent_with_complement():
    result = reconstruct_deal(
        {
            "N": [f"{rank}S" for rank in RANKS],
            "E": [f"{rank}H" for rank in RANKS],
            "S": [f"{rank}D" for rank in RANKS],
            "W": ["AC"],
        },
        [],
        deal_timestamp_seconds=11.0,
    )

    assert result["status"] == "RECONSTRUCTED_FULL"
    assert result["seat_counts"]["W"] == 13
    assert result["unique_cards"] == 52
    assert result["deal"]["card_provenance"]["W"]["VISUAL"] == ["AC"]
    assert len(result["deal"]["card_provenance"]["W"]["INFERRED_DECK_COMPLEMENT"]) == 12
    assert result["deal"]["derivations"][0]["known_cards_before_complement"] == ["AC"]


def test_teacher_card_outside_deal_window_does_not_enter_reconstruction():
    _, declarations = extract("У Запада туз треф.", start=10.0, end=12.0)
    result = reconstruct_deal(
        {},
        declarations,
        deal_timestamp_seconds=500.0,
        speech_window_seconds=30.0,
    )

    assert result["status"] == "PARTIAL_WITH_EVIDENCE"
    assert result["accepted_speech_cards"] == []
    assert result["rejected_speech_cards"][0]["reason"] == "OUTSIDE_DEAL_WINDOW"


def test_fourteenth_teacher_card_for_speech_completed_hand_fails_closed():
    segments = [
        teacher_segment(f"У Севера {name} пик.", start=10 + index, end=11 + index)
        for index, name in enumerate(
            (
                "туз", "король", "дама", "валет", "десятка", "девятка",
                "восьмёрка", "семёрка", "шестёрка", "пятёрка", "четвёрка",
                "тройка", "двойка",
            )
        )
    ]
    segments.append(teacher_segment("У Севера туз червей.", start=24, end=25))
    declarations = extract_teacher_card_declarations(segments)["declarations"]
    result = reconstruct_deal(
        {},
        declarations,
        deal_timestamp_seconds=20.0,
    )

    assert result["status"] == "UNRESOLVED_CONFLICT"
    assert result["conflicts"][0]["reason"] == "TEACHER_DECLARATION_EXCEEDS_HAND_CAPACITY"


def test_r265_contract_is_observed_only_and_default_production_is_unchanged():
    contract = json.loads(Path("ops/r265-reconstruction-contract.json").read_text())
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text()

    assert contract["revision"] == "3.1-free-r26.5"
    assert contract["production_allowed"] is False
    assert contract["active_production_revision"] == "3.1-free-r26.3"
    assert contract["evidence_precedence"]["visual_cards_checked_against_speech"] is False
    assert contract["reconstruction"]["partial_remaining_hand_completion_allowed"] is True
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r26.3"' in workflow
    assert "python run_drive_3_1_free_oidc_r265.py" in workflow


def test_independent_deck_oracle_checks_every_missing_seat_and_partial_size():
    """I2-style independent oracle for complement and provenance invariants."""
    rng = random.Random(265)
    for sample in range(24):
        shuffled = list(FULL_DECK)
        rng.shuffle(shuffled)
        expected = {
            seat: set(shuffled[index * 13 : (index + 1) * 13])
            for index, seat in enumerate("NESW")
        }
        for missing_seat in "NESW":
            for visible_count in range(13):
                visual = {
                    seat: (
                        sorted(expected[seat])
                        if seat != missing_seat
                        else sorted(expected[seat])[:visible_count]
                    )
                    for seat in "NESW"
                }
                result = reconstruct_deal(
                    visual,
                    [],
                    deal_timestamp_seconds=0.0,
                )
                assert result["status"] == "RECONSTRUCTED_FULL", (
                    sample,
                    missing_seat,
                    visible_count,
                )
                actual = {
                    seat: set(result["deal"]["hands"][seat]["cards"])
                    for seat in "NESW"
                }
                assert actual == expected
                assert result["unique_cards"] == 52
