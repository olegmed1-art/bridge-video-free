"""Local synthetic fixture only; no database, external source, or publication."""
import json
import unittest
from copy import deepcopy
from pathlib import Path

from bridge_contracts.book_material import validate_bundle
from tools.book_candidate_import.adapter import assess_illustrations, dry_run


HERE = Path(__file__).resolve().parents[1] / "tools" / "book_candidate_import"


class CandidateAdapterTests(unittest.TestCase):
    @staticmethod
    def duplicate_fixture():
        return json.loads((HERE / "fixtures/synthetic-duplicate-club-diagram.json").read_text(encoding="utf-8"))

    def test_existing_staging_contract_preserves_provenance_and_candidate_state(self):
        fixture = json.loads((HERE / "fixtures/synthetic-two-candidates.json").read_text(encoding="utf-8"))
        result = dry_run(fixture["candidates"], fixture["evidence"])
        bundle = result["bundle"]
        validate_bundle(bundle)
        self.assertEqual(result["state"], "STAGED_DRY_RUN")
        self.assertEqual(bundle["source"], fixture["evidence"]["source"])
        self.assertEqual(bundle["schema"], "book-material-staging-v1")
        self.assertEqual((bundle["processing"], bundle["publication"]), ("STAGED", "NOT_PUBLISHED"))
        self.assertFalse(result["student_delivery_allowed"])
        self.assertFalse(result["canon_promotion_allowed"])
        for trace, card, obj in zip(result["trace"], fixture["candidates"], bundle["objects"]):
            self.assertEqual(trace["candidate_version"], card["candidate_version"])
            self.assertEqual(trace["candidate_status"], "candidate_unpublished")
            self.assertEqual(trace["source_locator"], bundle["source"]["locator"])
            self.assertEqual(trace["supporting_printed_pages"], card["supporting_printed_pages"])
            self.assertEqual(trace["review_flags"], card["review_flags"])
            self.assertFalse(trace["instructional_use_allowed"])
            self.assertEqual(obj["anchor"], fixture["evidence"]["anchors"][card["candidate_id"]])
            self.assertEqual(obj["origin"], "INFERENCE")
            self.assertEqual((obj["review"], obj["verification"], obj["rights"]),
                             ("NOT_REVIEWED", "M0", "RESTRICTED"))
            self.assertNotIn("counterexample", obj["content"])

    def test_synthetic_duplicate_is_invalid_and_bound_to_fixture_source(self):
        evidence = self.duplicate_fixture()
        result = assess_illustrations(evidence)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "invalid")
        self.assertEqual(result[0]["diagnostic_code"], "DUPLICATE_CARD")
        self.assertEqual(result[0]["pdf_page_index"], 0)
        self.assertEqual(result[0]["printed_page_label"], "i")
        self.assertEqual(result[0]["source_rendition_sha256"],
                         evidence["source"]["rendition_sha256"])
        self.assertEqual(result[0]["source_segment_sha256"],
                         evidence["fragments"][0]["anchor"]["segment_sha256"])
        self.assertFalse(result[0]["included_in_bundle"])

    def test_invalid_illustration_does_not_block_independent_text(self):
        fixture = json.loads((HERE / "fixtures/synthetic-two-candidates.json").read_text(encoding="utf-8"))
        diagram = self.duplicate_fixture()
        fixture["evidence"]["fragments"] = diagram["fragments"]
        illustration = deepcopy(diagram["illustrations"][0])
        illustration["source_rendition_sha256"] = fixture["evidence"]["source"]["rendition_sha256"]
        fixture["evidence"]["illustrations"] = [illustration]
        result = dry_run(fixture["candidates"], fixture["evidence"])
        self.assertEqual(result["state"], "STAGED_DRY_RUN")
        self.assertEqual([obj["kind"] for obj in result["bundle"]["objects"]], ["ATOM", "ATOM"])
        self.assertEqual(result["illustration_diagnostics"][0]["diagnostic_code"], "DUPLICATE_CARD")
        self.assertFalse(result["student_delivery_allowed"])

    def test_partial_is_not_completed_and_full_claim_requires_52(self):
        evidence = self.duplicate_fixture()
        illustration = evidence["illustrations"][0]
        illustration["hands"] = {"N": {"C": ["A"]}, "S": {"C": ["K"]}}
        illustration["printed_hands"] = {"N": "♣A", "S": "♣K"}
        self.assertEqual(assess_illustrations(evidence)[0]["diagnostic_code"], "PARTIAL_ONLY")
        self.assertEqual(illustration["hands"], {"N": {"C": ["A"]}, "S": {"C": ["K"]}})
        illustration["claims_complete_deal"] = True
        self.assertEqual(assess_illustrations(evidence)[0]["diagnostic_code"], "EXACT_52_13_REQUIRED")
        ranks = list("AKQJT98765432")
        illustration["hands"] = {seat: {suit: ranks if suit == "SHDC"[i] else []
                                         for suit in "SHDC"}
                                 for i, seat in enumerate("NESW")}
        illustration["printed_hands"] = {seat: "♠♥♦♣"[i] + "AKQJT98765432"
                                         for i, seat in enumerate("NESW")}
        self.assertEqual(assess_illustrations(evidence)[0]["diagnostic_code"], "EXACT_52_13")

    def test_hand_overflow_is_invalid_even_for_partial_source(self):
        evidence = self.duplicate_fixture()
        evidence["illustrations"][0]["hands"] = {
            "N": {"S": list("AKQJT98765432"), "H": ["A"]}}
        evidence["illustrations"][0]["printed_hands"] = {"N": "♠AKQJT98765432♥A"}
        self.assertEqual(assess_illustrations(evidence)[0]["diagnostic_code"], "HAND_OVERFLOW")

    def test_printed_cards_cannot_be_silently_corrected(self):
        evidence = self.duplicate_fixture()
        evidence["illustrations"][0]["hands"]["S"]["C"] = ["Q", "4"]
        self.assertEqual(assess_illustrations(evidence)[0]["diagnostic_code"],
                         "TRANSCRIPTION_MISMATCH")


if __name__ == "__main__":
    unittest.main()
