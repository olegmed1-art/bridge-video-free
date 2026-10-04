"""Synthetic-only checks: no book text, real pages, credentials or database DSN."""
from copy import deepcopy
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bridge_contracts.book_material import (
    InvalidBookMaterial, digest, identity, produce_bundle, validate_bundle,
    validate_position,
)
from bridge_contracts.book_material_store import BookStagingStore


SOURCE = {"source_id": "SYNTHETIC-SOURCE", "edition_id": "SYNTHETIC-EDITION",
          "rendition_sha256": hashlib.sha256(b"synthetic rendition").hexdigest(),
          "title": "Synthetic bridge fixture (not a published book)",
          "locator": "https://example.invalid/synthetic-book", "page_count": 3}
RUN = {"run_id": "synthetic-run-1", "model_version": "none", "prompt_version": "none"}


def record(kind="ATOM", content=None, page=0):
    return {"kind": kind, "anchor": {"pdf_page_index": page, "printed_page_label": "iv",
            "bbox": [0.1, 0.2, 0.8, 0.9], "bbox_units": "NORMALIZED_TOP_LEFT",
            "segment_hash_method": "NORMALIZED_TEXT_UTF8_SHA256", "segment_sha256": digest("synthetic segment")},
            "content": content or {"statement": "Synthetic conditional claim.", "domain": "CARD_PLAY",
                                   "conditions": ["Fixture precondition"], "exceptions": ["Fixture exception"]},
            "origin": "AUTHOR", "rights": "INTERNAL_ALLOWED", "errata_ids": []}


def bundle(records=None, run=None):
    return produce_bundle(SOURCE, run or RUN, records or [record()])


def full_position():
    return {"position_type": "EXACT_FULL", "hands": {
        seat: {suit: list("AKQJT98765432") if suit == "SHDC"[i] else [] for suit in "SHDC"}
        for i, seat in enumerate("NESW")}}


class BookContractTests(unittest.TestCase):
    def test_axes_are_not_promoted_by_producer(self):
        item = record()
        item.update(review="REVIEWED", verification="M2")
        result = bundle([item])
        self.assertEqual(result["objects"][0]["review"], "NOT_REVIEWED")
        self.assertEqual(result["objects"][0]["verification"], "M0")
        self.assertEqual(result["publication"], "NOT_PUBLISHED")

    def test_identity_ignores_model_run_and_normalized_whitespace(self):
        changed = record()
        changed["content"]["statement"] = "  Synthetic  conditional claim.\n"
        a = bundle()
        b = bundle([changed], {**RUN, "run_id": "other", "model_version": "new-model"})
        self.assertEqual(a["objects"][0]["object_id"], b["objects"][0]["object_id"])
        changed["content"]["statement"] = "A different claim."
        self.assertNotEqual(a["objects"][0]["object_id"], bundle([changed])["objects"][0]["object_id"])

    def test_existing_ids_require_explicit_mapping(self):
        item = record()
        key = identity(SOURCE, item)
        result = produce_bundle(SOURCE, RUN, [item], legacy_ids={key: "EXISTING-ATOM-1"})
        self.assertEqual(result["objects"][0]["object_id"], "EXISTING-ATOM-1")

    def test_card_order_does_not_mint_new_identity(self):
        pos = record("POSITION", full_position())
        shuffled = deepcopy(pos)
        shuffled["content"]["hands"]["N"]["S"].reverse()
        self.assertEqual(bundle([pos])["objects"][0]["object_id"],
                         bundle([shuffled])["objects"][0]["object_id"])

    def test_missing_or_mistyped_relations_are_rejected(self):
        value = bundle()
        obj_id = value["objects"][0]["object_id"]
        value["relations"] = [{"from_id": obj_id, "to_id": "missing", "type": "SUPPORTS"}]
        with self.assertRaisesRegex(InvalidBookMaterial, "RELATION_FK"):
            validate_bundle(value)
        value["relations"] = [{"from_id": obj_id, "to_id": obj_id, "type": "ANSWERS"}]
        with self.assertRaisesRegex(InvalidBookMaterial, "ANSWER_RELATION_TYPE"):
            validate_bundle(value)

    def test_invalid_anchors_and_hashes_fail_closed(self):
        for field, value in [("pdf_page_index", True), ("pdf_page_index", 3),
                             ("pdf_page_index", -1), ("segment_sha256", ""),
                             ("bbox", [0.8, 0.2, 0.1, 0.9]), ("bbox", [0, 0, 2, 2]),
                             ("bbox", [False, 0, 1, 1])]:
            with self.subTest(field=field, value=value):
                item = record()
                item["anchor"][field] = value
                with self.assertRaises(InvalidBookMaterial):
                    bundle([item])

    def test_external_bidding_and_active_status_rejected(self):
        item = record()
        item["content"]["domain"] = "BIDDING"
        with self.assertRaises(InvalidBookMaterial):
            bundle([item])
        invalid = bundle()
        invalid["publication"] = "CANON_PROMOTED"
        with self.assertRaises(InvalidBookMaterial):
            validate_bundle(invalid)

    def test_exact_cards_and_unknown_slots_are_distinct(self):
        exact = full_position()
        self.assertTrue(validate_position(exact))
        exact["hands"]["N"]["S"][0] = "UNKNOWN"
        with self.assertRaisesRegex(InvalidBookMaterial, "EXACT_52_13"):
            validate_position(exact)
        exact["position_type"] = "PARTIAL"
        self.assertFalse(validate_position(exact))
        self.assertEqual(exact["hands"]["N"]["S"][0], "UNKNOWN")

    def test_duplicate_card_and_wrong_hand_size_rejected(self):
        invalid = full_position()
        invalid["hands"]["E"]["H"][0] = "K"
        with self.assertRaisesRegex(InvalidBookMaterial, "DUPLICATE_CARD"):
            validate_position(invalid)

    def test_in_trick_asymmetry_is_valid_without_inventing_cards(self):
        position = {"position_type": "END", "hands": {
            "N": {"S": list("KQJT")}, "E": {"H": list("KQJT")},
            "S": {"D": list("KQJT")}, "W": {"C": list("AKQJT")}},
            "play_state": {"trick_phase": "IN_TRICK", "tricks_completed": 8,
                "leader": "N", "next_player": "W", "inventory_complete": True,
                "played_cards": [{"seat": seat, "suit": suit, "rank": "A", "origin": "AUTHOR"}
                                 for seat, suit in zip("NES", "SHD")]}}
        before = deepcopy(position)
        self.assertFalse(validate_position(position))
        self.assertEqual(position, before)
        position["play_state"].update(trick_phase="BETWEEN_TRICKS", played_cards=[], next_player="N")
        with self.assertRaisesRegex(InvalidBookMaterial, "PHASE_CARD_COUNT"):
            validate_position(position)

    def test_phase_checks_order_and_already_played_card_duplicates(self):
        position = {"position_type": "PARTIAL", "hands": {"N": {"S": ["A"]}},
                    "play_state": {"trick_phase": "IN_TRICK", "tricks_completed": 8,
                    "leader": "N", "next_player": "E", "inventory_complete": False,
                    "played_cards": [{"seat": "N", "suit": "S", "rank": "A", "origin": "AUTHOR"}]}}
        with self.assertRaisesRegex(InvalidBookMaterial, "PLAYED_CARD_DUPLICATE"):
            validate_position(position)
        position["play_state"]["played_cards"][0].update(seat="S", rank="K")
        with self.assertRaisesRegex(InvalidBookMaterial, "PLAY_ORDER"):
            validate_position(position)

    def test_unknown_played_rank_is_preserved_without_inventing_exact_card(self):
        position = {"position_type": "PARTIAL", "hands": {"N": {"S": ["A"]}},
                    "play_state": {"trick_phase": "IN_TRICK", "tricks_completed": 8,
                    "leader": "N", "next_player": "E", "inventory_complete": False,
                    "played_cards": [{"seat": "N", "suit": "D", "rank": "UNKNOWN", "origin": "INFERENCE"}]}}
        original = deepcopy(position)
        self.assertFalse(validate_position(position))
        self.assertEqual(position, original)

    def test_unknown_printed_label_does_not_copy_pdf_number(self):
        item = record()
        item["anchor"].update(printed_page_label=None, bbox_units="PDF_POINTS_TOP_LEFT", bbox=[12, 24, 180, 250])
        value = bundle([item])
        self.assertIsNone(value["objects"][0]["anchor"]["printed_page_label"])

    def test_phase_inventory_all_leaders_and_trick_boundaries(self):
        ranks, seats, suits = "AKQJT98765432", "NESW", "SHDC"
        for completed in range(13):
            for leader in range(4):
                for acted in range(4):
                    already = {seats[(leader + i) % 4] for i in range(acted)}
                    hands = {seat: {suits[i]: list(ranks[completed + (seat in already):])}
                             for i, seat in enumerate(seats)}
                    played = [{"seat": seats[(leader + i) % 4], "suit": suits[(leader + i) % 4],
                               "rank": ranks[completed], "origin": "AUTHOR"} for i in range(acted)]
                    state = {"trick_phase": "IN_TRICK" if acted else "BETWEEN_TRICKS",
                             "tricks_completed": completed, "leader": seats[leader],
                             "next_player": seats[(leader + acted) % 4],
                             "inventory_complete": True, "played_cards": played}
                    with self.subTest(completed=completed, leader=leader, acted=acted):
                        self.assertFalse(validate_position({"position_type": "END", "hands": hands, "play_state": state}))
                        self.assertEqual(sum(len(next(iter(h.values()))) for h in hands.values()) + acted + 4 * completed, 52)
        invalid = full_position()
        invalid["hands"]["N"]["S"].pop()
        with self.assertRaisesRegex(InvalidBookMaterial, "EXACT_52_13"):
            validate_position(invalid)

    def test_links_and_solver_channels(self):
        pos = record("POSITION", full_position())
        pos_id = "BOOK-" + identity(SOURCE, pos)
        ex = record("EXERCISE", {"question": "Synthetic question?", "position_id": pos_id})
        ex_id = "BOOK-" + identity(SOURCE, ex)
        answer = record("AUTHOR_ANSWER", {"statement": "Synthetic author answer.", "exercise_id": ex_id})
        dds = record("DDS_RESULT", {"position_id": pos_id, "solver": "synthetic-solver",
                     "solver_version": "0", "input_sha256": digest(pos["content"]),
                     "claim": "Synthetic trick claim", "result": "Synthetic result, not computed"})
        dds["origin"] = "INFERENCE"
        single = record("SINGLE_DUMMY", {"position_id": pos_id, "statement": "Synthetic inference.",
                                         "available_information": "Only the visible hand"})
        result = bundle([pos, ex, answer, dds, single])
        self.assertEqual(len(result["objects"]), 5)
        dds["content"]["input_sha256"] = "0" * 64
        with self.assertRaisesRegex(InvalidBookMaterial, "DDS_INPUT_HASH"):
            bundle([pos, ex, answer, dds, single])
        ex["content"]["position_id"] = "missing"
        with self.assertRaisesRegex(InvalidBookMaterial, "FOREIGN_KEY"):
            bundle([ex])

    def test_errata_blocks_dds_without_discarding_source(self):
        pos = record("POSITION", full_position())
        pos["errata_ids"] = ["SYNTHETIC-ERRATUM"]
        dds = record("DDS_RESULT", {"position_id": "BOOK-" + identity(SOURCE, pos),
            "solver": "fixture", "solver_version": "0", "input_sha256": digest(pos["content"]),
            "claim": "fixture", "result": "fixture"})
        dds["origin"] = "INFERENCE"
        with self.assertRaisesRegex(InvalidBookMaterial, "DDS_REQUIRES_EXACT"):
            bundle([pos, dds])
        self.assertEqual(len(bundle([pos])["objects"]), 1)

    def test_bearer_query_not_stored_in_locator(self):
        source = {**SOURCE, "locator": "https://example.invalid/book?token=synthetic"}
        with self.assertRaisesRegex(InvalidBookMaterial, "CANONICAL_LOCATOR"):
            produce_bundle(source, RUN, [record()])


class BookStoreTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.store = BookStagingStore(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_rerun_and_new_model_preserve_identity(self):
        first = self.store.stage(bundle())
        self.assertEqual(first["new_ids"], 1)
        self.assertEqual(self.store.stage(bundle())["new_observations"], 0)
        second = self.store.stage(bundle(run={**RUN, "run_id": "new-run", "model_version": "new"}))
        self.assertEqual(second["new_ids"], 0)
        self.assertEqual(second["new_observations"], 1)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failure_rolls_back_all_local_tables(self):
        self.store.stage(bundle())
        changed = record()
        changed["content"]["statement"] = "Changed fixture."
        def fail():
            raise RuntimeError("synthetic interrupted commit")
        with self.assertRaises(RuntimeError):
            self.store.stage(bundle([changed], {**RUN, "run_id": "interrupted"}), before_commit=fail)
        for table in ("book_batch", "book_object", "book_observation", "book_outbox"):
            self.assertEqual(self.connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0], 1)
        self.assertEqual(self.store.stage(bundle([changed], {**RUN, "run_id": "interrupted"}))["new_ids"], 1)

    def test_replay_conflict_does_not_overwrite_history(self):
        self.store.stage(bundle())
        changed = record()
        changed["content"]["statement"] = "Different fixture."
        with self.assertRaisesRegex(ValueError, "RUN_REPLAY_CONFLICT"):
            self.store.stage(bundle([changed]))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM book_object").fetchone()[0], 1)

    def test_preview_has_exact_anchor_but_never_claims_source_proof(self):
        value = bundle()
        self.store.stage(value)
        preview = self.store.preview(RUN["run_id"], value["objects"][0]["object_id"])
        self.assertEqual(preview["citation"]["printed_page_label"], "iv")
        self.assertEqual(preview["citation"]["pdf_page_index"], 0)
        self.assertFalse(preview["source_bytes_verified"])
        self.assertFalse(preview["student_delivery_allowed"])
        self.store.retire(RUN["run_id"])
        with self.assertRaisesRegex(ValueError, "BATCH_NOT_AVAILABLE"):
            self.store.preview(RUN["run_id"], value["objects"][0]["object_id"])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM book_observation").fetchone()[0], 1)

    def test_reopen_retains_idempotency(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "staging.sqlite"
            conn = sqlite3.connect(path)
            BookStagingStore(conn).stage(bundle())
            conn.close()
            conn = sqlite3.connect(path)
            try:
                self.assertTrue(BookStagingStore(conn).stage(bundle())["replay"])
            finally:
                conn.close()

    def test_restricted_material_cannot_be_previewed(self):
        item = record()
        item["rights"] = "NEEDS_RIGHTS"
        value = bundle([item])
        self.store.stage(value)
        with self.assertRaisesRegex(InvalidBookMaterial, "PREVIEW_RIGHTS_REQUIRED"):
            self.store.preview(RUN["run_id"], value["objects"][0]["object_id"])

    def test_existing_legacy_identity_cannot_be_reminted(self):
        item = record()
        legacy = produce_bundle(SOURCE, RUN, [item], legacy_ids={identity(SOURCE, item): "LEGACY-1"})
        self.store.stage(legacy)
        with self.assertRaisesRegex(ValueError, "LEGACY_MAPPING_REQUIRED"):
            self.store.stage(bundle(run={**RUN, "run_id": "unmapped"}))
        self.assertEqual(self.connection.execute("SELECT count(*) FROM book_batch").fetchone()[0], 1)

    def test_payload_tampering_rejected_on_read(self):
        value = bundle()
        self.store.stage(value)
        with self.connection:
            self.connection.execute("UPDATE book_batch SET bundle_json='{}'")
        with self.assertRaisesRegex(ValueError, "STORED_BUNDLE_HASH_MISMATCH"):
            self.store.preview(RUN["run_id"], value["objects"][0]["object_id"])


if __name__ == "__main__":
    unittest.main()
