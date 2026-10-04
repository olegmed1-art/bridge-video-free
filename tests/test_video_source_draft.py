"""Entirely synthetic short statements and source identifiers, no private data."""
from copy import deepcopy
import hashlib
import sqlite3
import unittest

from bridge_contracts.video_canon_replay import replay_result_bundle, replay_requested
from tools.replay_video_canon import stage_local
from bridge_contracts.video_source_draft import normalize_source_packet


def packet(text="Develop diamonds because they provide 4 tricks."):
    return {
        "schema": "video-bounded-source-inspection-v1",
        "video_source_claim": {"drive_file_id": "synthetic-video", "source_sha256": "a" * 64},
        "source_document": {"drive_file_id": "synthetic-document", "embedded_raw_sha256": "b" * 64,
            "embedded_canonical_sort_compact_utf8_sha256": "c" * 64,
            "source_map_declared_master_sha256": "c" * 64},
        "selected_transcript_segment": {"field_path": "$.transcript[0]", "segment_id": "synthetic-segment",
            "start": 1, "end": 4, "exact_text": text,
            "exact_text_utf8_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "master_speaker_cluster": "synthetic-B", "master_role_candidate": "teacher"},
        "episode_context": {"field_path": "$.episodes[0]", "episode_id": "synthetic-episode", "start": 0, "end": 5},
        "speaker_identity_claim": {"cluster_id": "synthetic-B", "role": "teacher",
            "evidence_refs": ["synthetic-anchor"], "trusted_identity_resolver_pass": False},
        "frame_inventory_candidates": [],
    }


class SourceDraftTests(unittest.TestCase):
    def test_exact_source_and_quote_relation(self):
        data = packet()
        before = deepcopy(data)
        result = replay_result_bundle(data)
        draft = result["draft_knowledge"]
        self.assertEqual(data, before)
        self.assertTrue(replay_requested(data))
        self.assertEqual(draft["transcript"], data["selected_transcript_segment"])
        self.assertEqual(draft["speaker_claim"], data["speaker_identity_claim"])
        self.assertEqual(draft["semantic_domain"], "CARD_PLAY")
        self.assertEqual(draft["knowledge"]["claimed_tricks"], 4)
        for key in ("action", "reason"):
            start, end = draft["knowledge"]["quote_offsets"][key]
            self.assertEqual(draft["transcript"]["exact_text"][start:end], draft["knowledge"][key + "_quote"])
        self.assertTrue(all(value == "UNKNOWN" for value in draft["unknown"].values()))
        self.assertEqual(draft["knowledge"]["reason_quote"], "they provide 4 tricks")
        self.assertFalse(result["video_canon_inputs"])

    def test_generic_russian_statement_and_other_suit(self):
        data = packet("Разыгрывайте пику, потому что это даст 2 взятки.")
        draft = replay_result_bundle(data)["draft_knowledge"]
        self.assertEqual(draft["knowledge"]["suit"], "SPADES")
        self.assertEqual(draft["knowledge"]["claimed_tricks"], 2)
        self.assertEqual(draft["transcript"], data["selected_transcript_segment"])

    def test_repeated_staging_has_no_duplicates(self):
        first = replay_result_bundle(packet())
        second = replay_result_bundle(packet())
        self.assertEqual(first, second)
        with sqlite3.connect(":memory:") as connection:
            self.assertEqual(stage_local(connection, first)["inserted"], 1)
            self.assertEqual(stage_local(connection, second)["inserted"], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM replay_staging").fetchone()[0], 1)

    def test_mismatches_block_extraction(self):
        for section, field, value, code in (
            ("selected_transcript_segment", "exact_text_utf8_sha256", "e" * 64, "TEXT_DIGEST_MISMATCH"),
            ("source_document", "source_map_declared_master_sha256", "e" * 64, "PARENT_CANONICAL_DIGEST_MISMATCH"),
            ("video_source_claim", "source_sha256", "invalid", "DIGEST_INVALID"),
            ("speaker_identity_claim", "cluster_id", "other", "SPEAKER_CLUSTER_MISMATCH"),
            ("selected_transcript_segment", "end", 6, "SOURCE_INTERVAL_INVALID"),
        ):
            with self.subTest(code=code):
                data = packet()
                data[section][field] = value
                draft = replay_result_bundle(data)["draft_knowledge"]
                self.assertIsNone(draft["knowledge"])
                self.assertIn(code, {gap["code"] for gap in draft["gaps"]})

    def test_insufficient_or_negative_text_has_no_invented_rationale(self):
        for text in ("Develop diamonds.", "Do not develop diamonds because they provide 4 tricks.",
                     "Develop diamonds because they provide 4 tricks, but that is wrong.",
                     "Raise partner because of support."):
            draft = replay_result_bundle(packet(text))["draft_knowledge"]
            self.assertEqual(draft["extraction_status"], "EXTRACTION_AMBIGUOUS")
            self.assertIsNone(draft["knowledge"])
            self.assertEqual(draft["transcript"]["exact_text"], text)

    def test_selfclaims_and_even_injected_resolver_cannot_release_draft(self):
        data = packet()
        data.update({"authority": {"canon_activation_allowed": True}, "verification_receipts": {"status": "PASS"},
                     "trusted_manifest": {"evidence_class": "REAL"}, "semantic_domain": "BIDDING"})
        data["speaker_identity_claim"]["trusted_identity_resolver_pass"] = True
        def forbidden(_):
            raise AssertionError("draft must not call operating resolver")
        result = replay_result_bundle(data, evidence_resolver=forbidden)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["video_canon_inputs"])
        self.assertFalse(result["promotion_commands"])
        self.assertEqual(result["draft_knowledge"]["semantic_domain"], "CARD_PLAY")
        self.assertFalse(any(result["draft_knowledge"]["authority"].values()))
        self.assertIn("SOURCE_AUTHORIZATION_UNPROVEN", {gap["code"] for gap in result["gaps"]})

    def test_malformed_sections_are_gaps(self):
        for section in ("video_source_claim", "source_document", "selected_transcript_segment", "episode_context", "speaker_identity_claim"):
            data = packet()
            data[section] = []
            result = replay_result_bundle(data)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertIsNone(result["draft_knowledge"]["knowledge"])

    def test_questions_retractions_conditions_and_corrections_are_ambiguous(self):
        opening = packet()["selected_transcript_segment"]["exact_text"].rstrip(".")
        for suffix in ("?", ". That is wrong.", ". Correction: two tricks.", ", if the suit breaks well."):
            with self.subTest(suffix=suffix):
                draft = replay_result_bundle(packet(opening + suffix))["draft_knowledge"]
                self.assertIsNone(draft["knowledge"])
                self.assertEqual(draft["extraction_status"], "EXTRACTION_AMBIGUOUS")

    def test_huge_timestamp_and_wrong_schema_are_gaps(self):
        data = packet()
        data["selected_transcript_segment"]["end"] = 10 ** 400
        self.assertIsNone(replay_result_bundle(data)["draft_knowledge"]["knowledge"])
        for invalid in ([], {"schema": "unknown"}):
            draft = normalize_source_packet(invalid)
            self.assertIn("SOURCE_SCHEMA_INVALID", {gap["code"] for gap in draft["gaps"]})

    def test_unparsed_continuation_or_out_of_range_count_is_ambiguous(self):
        for text in ("Develop diamonds because they provide 0 tricks.",
                     "Develop diamonds because they provide 14 tricks.",
                     "Develop diamonds because they provide 4 tricks. Then play another suit."):
            self.assertIsNone(replay_result_bundle(packet(text))["draft_knowledge"]["knowledge"])

    def test_boundary_overlap_annotations_cannot_be_cleared_by_claims(self):
        constraints = {
            "example_end": 3, "excluded_next_segment_refs": ["synthetic-next"],
            "missing_episode_refs": ["synthetic-omitted"],
            "overlap": {"start": 1, "end": 2, "status": "RESOLVED"},
            "identity_lineage": {"legacy": "teacher", "newer": "MIXED"},
            "frame_boundary_conflicts": [{"time": 3, "status": "PASS"}],
        }
        data = packet()
        data["source_constraints"] = constraints
        result = replay_result_bundle(data)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["draft_knowledge"]["source_constraints"], constraints)
        self.assertIsNone(result["draft_knowledge"]["knowledge"])
        self.assertFalse(result["video_canon_inputs"])
        self.assertFalse(result["promotion_commands"])
        codes = {gap["code"] for gap in result["gaps"]}
        self.assertTrue({"OVERLAP_UNRESOLVED", "FRAME_BOUNDARY_CONFLICT",
                         "EXAMPLE_BOUNDARY_UNVERIFIED", "EPISODE_EVIDENCE_INCOMPLETE"} <= codes)
        self.assertEqual(result, replay_result_bundle(data))

    def test_constraints_block_already_normalized_contract_inputs(self):
        from tests.test_video_canon_replay import synthetic_bundle
        data = synthetic_bundle()
        data["source_constraints"] = {"overlap": {"status": "RESOLVED"}}
        def forbidden(_):
            raise AssertionError("unreviewed constraints must block before resolver")
        result = replay_result_bundle(data, evidence_resolver=forbidden)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["video_canon_inputs"])
        self.assertEqual(result["candidates"][0]["payload"]["source_constraints"], data["source_constraints"])

    def test_invalid_or_unknown_constraint_shapes_fail_closed(self):
        for constraints in (None, [], {}, {"unknown": "PASS"}):
            data = packet()
            data["source_constraints"] = constraints
            result = replay_result_bundle(data)
            self.assertIsNone(result["draft_knowledge"]["knowledge"])
            self.assertEqual(result["status"], "BLOCKED")

    def test_quality_cannot_route_constraints_or_bounded_schema_to_legacy(self):
        from unittest.mock import patch
        from diana_longitudinal_quality_v4_2 import build_quality_layer
        from tests.test_diana_longitudinal_quality_v4_2 import base_master
        from tests.test_video_canon_replay import synthetic_bundle, replay_result_bundle as synthetic_replay
        inputs = synthetic_replay(synthetic_bundle())["video_canon_inputs"]
        for marker in ({"source_constraints": {"overlap": {"status": "RESOLVED"}}},
                       {"source_constraints": None},
                       {"schema": "video-bounded-source-inspection-v1"}):
            with patch("diana_longitudinal_quality_v4_2.run_video_canon_auto_pipeline") as legacy:
                quality = build_quality_layer({**base_master(), **inputs, **marker})
                legacy.assert_not_called()
                result = quality["video_canon_auto_pipeline"]
                self.assertEqual(result["status"], "BLOCKED")
                self.assertFalse(result["promotion_commands"])
                self.assertEqual(quality["counts"]["video_canon_candidates"], 0)
        self.assertTrue(replay_requested({"source_constraints": None}))


if __name__ == "__main__":
    unittest.main()
