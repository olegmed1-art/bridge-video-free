"""Modality and source/board binding regressions on existing ASR payload shape."""
from copy import deepcopy
import unittest
from bridge_vision.teacher_card_reconstruction_r265 import (
    classify_speech_modality, declaration_digest, extract_teacher_card_declarations, reconstruct_deal,
)


def segment(text):
    return {"segment_id": "seg-00012", "start": 10.25, "end": 12.75, "text": text,
            "speaker": "SPEAKER_A", "speaker_role_candidate": "teacher",
            "speaker_role_confidence": .99, "unreliable": False}


SCOPE = {"source_video_sha256": "a" * 64, "episode_id": "episode-1", "board_epoch": 1,
         "board_number": 12, "deal_start_ms": 9000, "deal_end_ms": 15000}


def review(item):
    return {**SCOPE, "declaration_sha256": declaration_digest(item), "modality": "ACTUAL_HOLDING",
            "audio_review_ref": "independent-audio-review", "speaker_review_ref": "independent-speaker-review",
            "evidence_ref": "audio-" + declaration_digest(item)}


class SpeechTests(unittest.TestCase):
    def test_belief_conditional_and_other_board_context_are_not_assertions(self):
        for text, modality in (("I think West has ace of spades", "UNKNOWN"),
                ("West has ace of spades unless he holds a void", "HYPOTHETICAL"),
                ("West has ace of spades in the last board", "QUOTED_OTHER_BOARD"),
                ("Я думаю у Востока туз пик", "UNKNOWN")):
            self.assertEqual(classify_speech_modality(text), modality)
            self.assertEqual(extract_teacher_card_declarations([segment(text)])["declarations"], [])

    def test_target_time_outside_bound_board_is_rejected(self):
        declaration = extract_teacher_card_declarations([segment("West has ace of spades")])["declarations"][0]
        result = reconstruct_deal({}, [declaration], deal_timestamp_seconds=16,
            deal_identity=SCOPE, speech_reviews=[review(declaration)])
        self.assertEqual(result["accepted_speech_cards"], [])
    def test_negation_hypothesis_question_self_correction_abstain(self):
        for text in ("У Запада нет туза пик", "Если у Запада туз пик", "У Запада туз пик?",
                     "У Запада туз пик, нет, король пик", "Если бы у Востока был туз пик",
                     "West does not hold the ace of spades", "Suppose West has ace of spades",
                     "West has ace of spades or king of spades"):
            result = extract_teacher_card_declarations([segment(text)])
            self.assertEqual(result["declarations"], [], text)
            self.assertEqual(result["rejected"][0]["reason"], "NON_ASSERTIVE_OR_CORRECTED_SPEECH", text)

    def test_exact_assertion_is_candidate_until_independently_bound(self):
        declaration = extract_teacher_card_declarations([segment("У Запада туз пик")])["declarations"][0]
        self.assertEqual(declaration["modality"], "ASSERTION")
        self.assertEqual(declaration["evidence_locator"], "transcript.jsonl#segment=0")
        self.assertEqual((declaration["start"], declaration["end"]), (10.25, 12.75))
        result = reconstruct_deal({}, [declaration], deal_timestamp_seconds=11)
        self.assertEqual(result["accepted_speech_cards"], [])
        self.assertEqual(result["deal"]["hands"]["W"]["cards"], [])
        result = reconstruct_deal({}, [declaration], deal_timestamp_seconds=11,
                                  deal_identity=SCOPE, speech_reviews=[review(declaration)])
        self.assertEqual(result["deal"]["card_provenance"]["W"]["TEACHER_SPEECH"], ["AS"])
        self.assertEqual(result["recovery"]["deal"]["hands"]["W"]["observed_cards"], [])
        self.assertEqual(result["recovery"]["deal"]["hands"]["W"]["declared_cards"], ["AS"])

    def test_nearby_wrong_source_board_occurrence_and_speech_interval_are_rejected(self):
        d = extract_teacher_card_declarations([segment("У Запада туз пик")])["declarations"][0]
        for field, value in (("source_video_sha256", "b" * 64), ("episode_id", "episode-2"),
                             ("board_epoch", 2), ("board_number", 13), ("deal_start_ms", 13000)):
            scope = dict(SCOPE, **{field: value})
            result = reconstruct_deal({}, [d], deal_timestamp_seconds=14,
                                      deal_identity=scope, speech_reviews=[review(d)])
            self.assertEqual(result["accepted_speech_cards"], [], field)
        changed = deepcopy(d)
        changed["text"] = "different ASR transcript"
        result = reconstruct_deal({}, [changed], deal_timestamp_seconds=11,
                                  deal_identity=SCOPE, speech_reviews=[review(d)])
        self.assertEqual(result["accepted_speech_cards"], [])

    def test_booleans_nan_and_partial_cards_never_form_reviewed_facts(self):
        for text in ("У Востока туз", "У него туз пик"):
            self.assertEqual(extract_teacher_card_declarations([segment(text)])["declarations"], [])
        d = extract_teacher_card_declarations([segment("У Запада туз пик")])["declarations"][0]
        for value in (None, float("nan"), True):
            changed = dict(d, start=value)
            result = reconstruct_deal({}, [changed], deal_timestamp_seconds=11,
                                      deal_identity=SCOPE, speech_reviews=[])
            self.assertEqual(result["accepted_speech_cards"], [])

    def test_modality_is_rank_suit_independent_and_unknown_is_conservative(self):
        for card in ("туз пик", "десятка пик", "король червей", "дама бубен", "двойка треф"):
            for text, expected in ((f"Если бы у Востока была {card}", "HYPOTHETICAL"),
                                   (f"У Востока нет {card}", "NEGATION"),
                                   (f"У Востока {card}?", "QUESTION"),
                                   (f"У Востока {card}, точнее другая карта", "CORRECTION"),
                                   (f"На прошлой сдаче у Востока {card}", "QUOTED_OTHER_BOARD"),
                                   (f"У Востока {card}", "ASSERTION"),
                                   (f"Может быть у Востока {card}", "UNKNOWN"),
                                   (f"Смотрим на {card}", "UNKNOWN")):
                self.assertEqual(classify_speech_modality(text), expected, text)


if __name__ == "__main__":
    unittest.main()
