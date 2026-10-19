"""Targeted synthetic port tests only; no existing checkout, DB, HTTP or real IDs."""
from copy import deepcopy
import unittest

from evolutionary_course.book_teacher_consumer import (
    BOOK_CONTRACT, KNOWLEDGE_CONTRACT, TOPICS, BookTeacherBindingError,
    bind_import_receipt, build_card_map, consume_card,
)

SYNTHETIC_SOURCE = "00000000-0000-4000-8000-000000000099"
SYNTHETIC_CHANGE = "00000000-0000-4000-8000-000000000098"
SYNTHETIC_VERSIONS = ["00000000-0000-4000-8000-00000000000" + str(i) for i in (1, 2, 3)]


def packet():
    source = {"source_id": "SYNTHETIC-DESCRIPTOR", "edition_id": "SYNTHETIC-EDITION",
              "rendition_sha256": "a" * 64, "title": "Synthetic-only book fixture",
              "locator": "https://example.invalid/synthetic", "registry_source_id": SYNTHETIC_SOURCE}
    value = {"content_contract": BOOK_CONTRACT, "source": source,
             "run": {"run_id": "SYNTHETIC-UNIT-TEST-NOT-AN-IMPORT"},
             "import_state": {"knowledge_import_committed": False, "observed_import_receipt": None},
             "review_evidence_summary": {"fixture": "SYNTHETIC_ONLY"},
             "expected_import_receipt": {"schema": BOOK_CONTRACT, "changeset_id": None,
                 "version_ids": [None, None, None], "new_versions": None, "authority_lane": "WORLD_EXTERNAL",
                 "publication_hash": None, "outbox_delivery": "REQUIRES_READBACK",
                 "canon_activation_performed": False, "dry_run": False, "committed": None}, "cards": []}
    for i, label in enumerate(("E01", "E02", "E03")):
        citation = {**{k: source[k] for k in ("source_id", "edition_id", "rendition_sha256", "title", "locator")},
                    "pdf_page_index": i, "printed_page_label": str(i + 1), "bbox": [0.1, 0.2, 0.8, 0.9],
                    "bbox_units": "NORMALIZED_TOP_LEFT", "segment_sha256": "b" * 64,
                    "segment_hash_method": "NORMALIZED_TEXT_UTF8_SHA256"}
        value["cards"].append({"card_label": label, "candidate_id": "SYNTHETIC-" + label,
             "stable_key": "SYNTHETIC-BOOK-" + label, "object_id": "SYNTHETIC-BOOK-" + label,
             "name": "Synthetic " + label, "skill": "Synthetic skill " + label,
             "statement": "Synthetic bounded claim " + label, "conditions": ["Synthetic condition"],
             "exceptions": ["Synthetic exception"], "citation": citation,
             "source_pages": {"pdf_page_index_0based": i, "pdf_page_number_1based": i + 1,
                              "printed_page_label": str(i + 1)},
             "origin": "INFERENCE", "verification": "M2", "source_review_id": "SYNTHETIC-REVIEW",
             "knowledge_item_id": None, "knowledge_version_id": None})
    return value


def bound_map():
    value = build_card_map(packet())
    receipt = {**value["expected_import_receipt"], "changeset_id": SYNTHETIC_CHANGE,
               "version_ids": SYNTHETIC_VERSIONS[:], "new_versions": 3,
               "publication_hash": "c" * 64, "committed": True}
    event = {"schema": BOOK_CONTRACT, "publication_hash": "c" * 64, "run": value["run"],
             "source_id": SYNTHETIC_SOURCE, "stable_keys": [c["stable_key"] for c in value["cards"]],
             "version_ids": SYNTHETIC_VERSIONS[:]}
    return bind_import_receipt(value, receipt, event)


def context(label="E01"):
    return {"topic": TOPICS[label], "stage": "INITIAL_PLAN", "dummy_exposed": True,
            "opening_lead_made": True, "asks_for": "PRINCIPLE_EXPLANATION"}


def item_for(card, state="active"):
    return {"stable_key": card["stable_key"], "item_id": card["knowledge_version_id"],
            "version_status": state, "review_status": "reviewed", "authority_class": "external",
            "system_profile": "SYSTEM_NEUTRAL", "content": {"schema": BOOK_CONTRACT, **deepcopy(card["expected_content"])},
            "provenance": {"source_id": SYNTHETIC_SOURCE}}


def query_result(items):
    return {"contract_version": KNOWLEDGE_CONTRACT, "authority_lane": "WORLD_EXTERNAL",
            "system_profile": "SYSTEM_NEUTRAL", "retrieval_status": "WORLD_MATCH" if items else "WORLD_GAP",
            "count": len(items), "items": items, "fallback_performed": False,
            "candidate_activation_performed": False}


def renderer_port_fixture(item):
    # This fixture models the public output port. It does not attest real source review.
    c = item["content"]
    return {"contract_version": BOOK_CONTRACT, "authority_lane": "WORLD_EXTERNAL",
            "knowledge_version_id": item["item_id"], "stable_key": item["stable_key"],
            "answer": c["statement"], "conditions": c["conditions"], "exceptions": c["exceptions"],
            "verification": c["verification"], "citations": [deepcopy(c["citation"])],
            "source_review_id": "SYNTHETIC-REVIEW", "pdf_page_number_1based": c["citation"]["pdf_page_index"] + 1,
            "canon_activation_performed": False, "fallback_performed": False}


class ConsumerTests(unittest.TestCase):
    def fail_if_called(self, *args, **kwargs):
        self.fail("No reader/renderer call was allowed for this case")

    def run_card(self, value, card, items, renderer=renderer_port_fixture, ctx=None):
        return consume_card(value, card["card_label"], ctx or context(card["card_label"]),
                            query_knowledge=lambda **kwargs: query_result(items), render_teacher_book=renderer)

    def test_map_preserves_empty_new_ids_and_descriptor_identity(self):
        value = build_card_map(packet())
        for card in value["cards"]:
            self.assertIsNone(card["knowledge_item_id"])
            self.assertIsNone(card["knowledge_version_id"])
            self.assertIsNone(card["school_catalog_skill_id"])
            self.assertEqual(card["expected_content"]["citation"]["source_id"], "SYNTHETIC-DESCRIPTOR")

    def test_all_three_uncommitted_cards_gap_without_retrieval(self):
        value = build_card_map(packet())
        for card in value["cards"]:
            result = consume_card(value, card["card_label"], context(card["card_label"]),
                                  query_knowledge=self.fail_if_called, render_teacher_book=self.fail_if_called)
            self.assertEqual(result["reason"], "IMPORT_NOT_COMMITTED")
            self.assertIsNone(result["answer"])
            self.assertEqual(result["citations"], [])

    def test_receipt_template_is_not_an_observed_commit(self):
        value = build_card_map(packet())
        with self.assertRaisesRegex(BookTeacherBindingError, "IMPORT_NOT_COMMITTED"):
            bind_import_receipt(value, value["expected_import_receipt"], {})

    def test_bind_by_event_keys_not_card_order(self):
        value = bound_map()
        receipt, event = deepcopy(value["observed_import_receipt"]), deepcopy(value["observed_recorded_event_payload"])
        event["stable_keys"] = event["stable_keys"][::-1]
        event["version_ids"] = event["version_ids"][::-1]
        receipt["version_ids"] = event["version_ids"][:]
        result = bind_import_receipt(build_card_map(packet()), receipt, event)
        self.assertEqual([c["knowledge_version_id"] for c in result["cards"]], SYNTHETIC_VERSIONS)

    def test_binding_rejects_dry_run_and_false_commit(self):
        value = bound_map()
        for field, bad in (("dry_run", True), ("committed", False)):
            receipt = deepcopy(value["observed_import_receipt"]); receipt[field] = bad
            with self.subTest(field=field), self.assertRaises(BookTeacherBindingError):
                bind_import_receipt(build_card_map(packet()), receipt, value["observed_recorded_event_payload"])

    def test_binding_rejects_wrong_source_run_hash_and_card_set(self):
        value = bound_map()
        for field, bad in (("source_id", "wrong"), ("run", {}), ("publication_hash", "d" * 64), ("stable_keys", ["wrong"] * 3)):
            event = deepcopy(value["observed_recorded_event_payload"]); event[field] = bad
            with self.subTest(field=field), self.assertRaises(BookTeacherBindingError):
                bind_import_receipt(build_card_map(packet()), value["observed_import_receipt"], event)

    def test_binding_rejects_duplicate_and_non_uuid_version_ids(self):
        value = bound_map()
        for bad in ([SYNTHETIC_VERSIONS[0]] * 3, [None] * 3, ["PLANNED-NOT-OBSERVED"] * 3):
            receipt = deepcopy(value["observed_import_receipt"]); receipt["version_ids"] = bad
            with self.subTest(bad=bad), self.assertRaises(BookTeacherBindingError):
                bind_import_receipt(build_card_map(packet()), receipt, value["observed_recorded_event_payload"])

    def test_binding_does_not_mutate_inputs(self):
        value = bound_map(); before = deepcopy(value)
        bind_import_receipt(value, value["observed_import_receipt"], value["observed_recorded_event_payload"])
        self.assertEqual(value, before)

    def test_committed_but_world_empty_is_still_gap(self):
        value = bound_map()
        self.assertEqual(self.run_card(value, value["cards"][0], [])["reason"], "BOOK_WORLD_GAP")

    def test_staged_candidate_and_archived_never_reach_renderer(self):
        value = bound_map()
        for state in ("staged", "candidate", "archived"):
            card = value["cards"][0]
            with self.subTest(state=state):
                result = self.run_card(value, card, [item_for(card, state)], renderer=self.fail_if_called)
                self.assertEqual(result["reason"], "BOOK_WORLD_NOT_ACTIVE_REVIEWED")

    def test_all_three_positive_ports_retain_skill_and_correct_page(self):
        value = bound_map()
        for card in value["cards"]:
            with self.subTest(label=card["card_label"]):
                result = self.run_card(value, card, [item_for(card)])
                self.assertEqual(result["status"], "EXPLANATION_READY")
                self.assertIn("с. " + card["expected_content"]["citation"]["printed_page_label"], result["explanation_with_page"])
                self.assertEqual(result["skill"]["label"], card["skill_label"])
                self.assertIsNone(result["skill"]["school_catalog_skill_id"])
                self.assertFalse(result["canon_activation_performed"])

    def test_standalone_explanation_retains_every_condition_and_exception(self):
        value = bound_map()
        for card in value["cards"]:
            label = card["card_label"]
            expected = card["expected_content"]
            expected["conditions"] = ["Dummy exposed for " + label,
                                      "Opening lead made for " + label]
            expected["exceptions"] = ["No automatic contract guarantee for " + label,
                                      "Entries and tempo need separate evaluation for " + label]
            item = item_for(card)
            before = deepcopy((value, item))
            with self.subTest(label=label):
                result = self.run_card(value, card, [item])
                self.assertEqual(result["status"], "EXPLANATION_READY")
                text = result["explanation_with_page"]
                for bound in expected["conditions"] + expected["exceptions"]:
                    self.assertIn(bound, text)
                self.assertIn(expected["statement"], text)
                self.assertIn(expected["citation"]["title"], text)
                self.assertEqual(result["book_response"], renderer_port_fixture(item))
                self.assertFalse(result["mastery_event_created"])
                self.assertEqual((value, item), before)

    def test_renderer_cannot_drop_conditions_or_exceptions_before_text_output(self):
        value = bound_map(); card = value["cards"][2]
        for field in ("conditions", "exceptions"):
            def incomplete_renderer(item):
                response = renderer_port_fixture(item)
                response[field] = []
                return response
            with self.subTest(field=field):
                result = self.run_card(value, card, [item_for(card)], renderer=incomplete_renderer)
                self.assertEqual(result["reason"], "BOOK_RENDERER_RESPONSE_BINDING_MISMATCH")
                self.assertNotIn("explanation_with_page", result)

    def test_pinned_version_source_and_content_drift_block(self):
        value = bound_map(); card = value["cards"][0]
        for field in ("version", "source", "content"):
            item = item_for(card)
            if field == "version": item["item_id"] = SYNTHETIC_VERSIONS[1]
            if field == "source": item["provenance"]["source_id"] = "wrong"
            if field == "content": item["content"]["statement"] = "changed"
            with self.subTest(field=field):
                self.assertEqual(self.run_card(value, card, [item], renderer=self.fail_if_called)["status"], "GAP")

    def test_existing_renderer_rejection_does_not_fallback(self):
        def rejecting(item): raise ValueError("Synthetic evidence rejected")
        value = bound_map(); card = value["cards"][0]
        self.assertEqual(self.run_card(value, card, [item_for(card)], renderer=rejecting)["reason"], "BOOK_WORLD_EVIDENCE_INVALID")

    def test_renderer_changed_page_and_canon_flag_block(self):
        value = bound_map(); card = value["cards"][0]
        for field, bad in (("pdf_page_number_1based", 999), ("canon_activation_performed", True), ("source_review_id", "wrong")):
            def bad_renderer(item):
                result = renderer_port_fixture(item); result[field] = bad; return result
            with self.subTest(field=field):
                self.assertEqual(self.run_card(value, card, [item_for(card)], renderer=bad_renderer)["reason"], "BOOK_RENDERER_RESPONSE_BINDING_MISMATCH")

    def test_canon_request_never_reads_world(self):
        value = bound_map()
        result = consume_card(value, "E01", context(), authority_lane="ACTIVE_SCHOOL_CANON",
                              query_knowledge=self.fail_if_called, render_teacher_book=self.fail_if_called)
        self.assertEqual(result["reason"], "NO_CANON_FALLBACK_FROM_WORLD_BOOK")

    def test_missing_or_false_applicability_is_honest_gap(self):
        value = bound_map()
        for field, bad in (("dummy_exposed", None), ("opening_lead_made", False), ("topic", "OTHER"), ("stage", "LATER_PLAY")):
            ctx = context(); ctx[field] = bad
            with self.subTest(field=field):
                result = consume_card(value, "E01", ctx, query_knowledge=self.fail_if_called, render_teacher_book=self.fail_if_called)
                self.assertEqual(result["status"], "GAP")

    def test_principle_cannot_be_used_as_specific_card_or_contract_guarantee(self):
        value = bound_map()
        for asks in ("SPECIFIC_CARD", "CONTRACT_GUARANTEE", None):
            ctx = context(); ctx["asks_for"] = asks
            result = consume_card(value, "E01", ctx, query_knowledge=self.fail_if_called, render_teacher_book=self.fail_if_called)
            self.assertEqual(result["reason"], "SPECIFIC_CARD_OR_CONTRACT_DECISION_NOT_SUPPORTED")

    def test_ambiguous_result_does_not_choose_first(self):
        value = bound_map(); card = value["cards"][0]; item = item_for(card)
        self.assertEqual(self.run_card(value, card, [item, deepcopy(item)])["reason"], "BOOK_WORLD_VERSION_AMBIGUITY")

    def test_query_error_does_not_echo_private_error_text(self):
        def unavailable(**kwargs): raise RuntimeError("SYNTHETIC-SECRET-MUST-NOT-LEAK")
        value = bound_map()
        result = consume_card(value, "E01", context(), query_knowledge=unavailable, render_teacher_book=self.fail_if_called)
        self.assertEqual(result["reason"], "KNOWLEDGE_READER_UNAVAILABLE")
        self.assertNotIn("SYNTHETIC-SECRET", str(result))

    def test_reader_contract_and_lane_are_explicit(self):
        value = bound_map(); card = value["cards"][1]; seen = []
        def reader(**kwargs): seen.append(kwargs); return query_result([item_for(card)])
        consume_card(value, "E02", context("E02"), query_knowledge=reader, render_teacher_book=renderer_port_fixture)
        self.assertEqual(seen, [{"lane": "WORLD_EXTERNAL", "system_profile": "SYSTEM_NEUTRAL",
                                "stable_key": card["stable_key"], "scope_key": "default", "limit": 2, "offset": 0}])

    def test_invalid_reader_envelope_blocks(self):
        value = bound_map(); card = value["cards"][0]
        for field, bad in (("authority_lane", "SOURCE"), ("contract_version", "other"), ("count", True), ("fallback_performed", True), ("retrieval_status", "CANON_MATCH")):
            envelope = query_result([item_for(card)]); envelope[field] = bad
            with self.subTest(field=field):
                result = consume_card(value, "E01", context(), query_knowledge=lambda **kwargs: envelope, render_teacher_book=self.fail_if_called)
                self.assertEqual(result["reason"], "KNOWLEDGE_ENVELOPE_INVALID")


if __name__ == "__main__":
    unittest.main()
