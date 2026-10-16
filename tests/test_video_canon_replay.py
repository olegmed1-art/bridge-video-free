"""Synthetic fixtures only; no lesson data or real identity receipts."""
from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from bridge_contracts.video_canon_replay import replay_result_bundle as replay_untrusted, replay_requested
from tools.replay_video_canon import stage_local


def synthetic_bundle():
    statement = "Preserve bidding space because partner must describe shape."
    sha = hashlib.sha256(statement.encode()).hexdigest()
    source = {"source_sha256": "a" * 64, "video_file_id": "synthetic-video",
              "source_name": "synthetic.mp4", "source_fingerprint": "synthetic-source"}
    observation = {
        "source": source,
        "observed_episode": {
            "interaction_id": "synthetic-episode", "start": 0, "end": 3,
            "task": "synthetic task", "student_action": "synthetic action",
            "teacher_intervention": statement, "student_followup": "synthetic followup",
            "observed_outcome": "synthetic outcome", "actor_attribution_status": "SUPPORTED",
        },
        "transcript_evidence": [{"locator": "synthetic#1", "start": 0, "end": 2,
            "text_sha256": sha, "speaker_id": "synthetic-teacher",
            "speaker_identity_status": "VERIFIED"}],
        "frame_evidence": [{"schema": "bridge-speech-frame-binding-v1",
            "method": "EXPLICIT_FRAME_SHA256", "frame_sha256": "b" * 64,
            "frame_file": "synthetic.jpg", "frame_time": 1, "speech_start": 0,
            "speech_end": 2, "transcript_locator": "synthetic#1",
            "distance_to_midpoint_seconds": 0, "source_fingerprint": "synthetic-source",
            "single_frame_binding": True}],
        "bridge_context": {field: {"status": "UNKNOWN", "value": None, "source_refs": []}
            for field in ("board", "dealer", "vulnerability", "auction", "deal")},
        "preliminary_skill": {"label": "synthetic skill", "status": "PROPOSED"},
        "confidence": {field: .99 for field in (
            "transcript", "frame", "actor_attribution", "bridge_context", "preliminary_skill")},
        "provenance": {"algorithm_revision": "synthetic-v1",
                       "contract_version": "video31-learning-candidate-v1"},
        "unresolved_questions": ["Synthetic context remains unknown"],
    }
    assertion = {
        "assertion_id": "synthetic-assertion", "statement": statement,
        "statement_sha256": sha, "speaker_id": "synthetic-teacher",
        "transcript_locators": ["synthetic#1"], "source_class": "TEACHING_CONTEXT",
        "source_authorization": {"status": "NOT_APPROVED", "decision_ref": "",
            "policy_version": "", "authorized_source_sha256": "",
            "authorized_video_file_id": "", "authorized_teacher_ids": [],
            "approved_semantic_scopes": [], "authorization_evidence_sha256": ""},
        "semantic_scope": "synthetic/scope", "semantic_confidence": .99,
        "ambiguities": [], "contradictions": [],
        "normalized_rule": {
            **{field: {} for field in ("auction_pattern", "hand_constraints",
                "public_context_constraints", "action", "meaning", "public_inference",
                "alert_semantics", "forcing_semantics", "compiled_payload")},
            "rule_key": "synthetic-rule", "rule_kind": "bid", "priority": 1,
            "specificity": 1, "condition_schema_version": "synthetic-v1",
            "method_version": "synthetic-v1"},
        "explanation": {"why_or_purpose": ["Preserve bidding space"],
            "consequences": ["partner must describe shape"],
            "rejected_alternatives": [], "evidence_refs": ["synthetic#1"]},
        "tests": {kind: [{"synthetic": True, "expect": "SYNTHETIC"}]
            for kind in ("positive", "negative", "boundary", "interference")},
    }
    return {"learning_observation": observation, "teacher_assertions": [assertion],
            "replay_provenance": {"parent_artifact_sha256": "c" * 64,
                                  "parent_hash_domain": "RAW_BYTES_SHA256"}}


def simulated_resolver(parent_sha):
    """Test double for a trusted reader, never a real source or verifier PASS."""
    observation = synthetic_bundle()["learning_observation"]
    if parent_sha != "c" * 64:
        return None
    return {
        "schema": "video-canon-replay-parent-evidence-v1", "evidence_class": "REAL",
        "parent_artifact_sha256": parent_sha, "parent_hash_domain": "RAW_BYTES_SHA256",
        "source": observation["source"],
        "transcripts": [{**observation["transcript_evidence"][0], "speaker_role": "teacher",
                         "frame_evidence": observation["frame_evidence"][0]}],
        "source_authorizations": [],
    }


def replay_result_bundle(bundle):
    return replay_untrusted(bundle, evidence_resolver=simulated_resolver)


class ReplayTests(unittest.TestCase):
    def test_synthetic_candidate_inputs_revalidate_without_authority(self):
        from bridge_contracts.video_canon_evidence import build_video_canon_candidate
        bundle = synthetic_bundle()
        original = deepcopy(bundle)
        result = replay_result_bundle(bundle)
        self.assertEqual(bundle, original)
        self.assertEqual(result["status"], "STAGING_READY")
        inputs = result["video_canon_inputs"]
        self.assertEqual(inputs["video_canon_verification_bundles"], {})
        self.assertEqual(build_video_canon_candidate(inputs["video_canon_learning_candidate"],
            inputs["video_canon_assertions"][0]), result["candidates"][0])
        self.assertEqual(result["candidates"][0]["quality_status"], "EVIDENCE_ONLY")
        self.assertEqual(result["promotion_commands"], [])

    def test_replay_twice_same_hash_no_duplicates(self):
        for bundle in (synthetic_bundle(), {"schema": "diana-longitudinal-extraction",
                "quality_v2": {"readiness": {"status": "METHODOLOGY_READY"}}}):
            with self.subTest(bundle_type=bundle.get("schema", "synthetic")):
                a, b = replay_result_bundle(bundle), replay_result_bundle(deepcopy(bundle))
                self.assertEqual(a, b)
                with sqlite3.connect(":memory:") as connection:
                    count = len(a["candidates"])
                    self.assertEqual(stage_local(connection, a)["inserted"], count)
                    self.assertEqual(stage_local(connection, b), {"inserted": 0, "existing": count})
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM replay_staging").fetchone()[0], count)

    def test_missing_and_partial_requests_are_explicit(self):
        self.assertFalse(replay_requested({"status": "COMPLETED"}))
        for bundle in ({"video_canon_analysis_requested": True},
                       {"video_canon_assertions": []}):
            self.assertTrue(replay_requested(bundle))
            result = replay_result_bundle(bundle)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertIn("learning_observation.frame_evidence", [g["path"] for g in result["gaps"]])
            self.assertEqual(result["video_canon_inputs"], {})

    def test_tampering_fails_closed(self):
        mutations = [
            lambda b: b["learning_observation"]["frame_evidence"][0].update(source_fingerprint="wrong"),
            lambda b: b["learning_observation"]["frame_evidence"][0].update(frame_time=9),
            lambda b: b["learning_observation"]["transcript_evidence"][0].update(speaker_identity_status="UNKNOWN"),
            lambda b: b["teacher_assertions"][0].update(statement="changed"),
            lambda b: b["teacher_assertions"][0]["explanation"].update(consequences=["invented consequence"]),
            lambda b: b["teacher_assertions"][0]["tests"].update(boundary=[]),
            lambda b: b["teacher_assertions"][0]["normalized_rule"].update(partner_hand="AKQ"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                bundle = synthetic_bundle()
                mutate(bundle)
                result = replay_result_bundle(bundle)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["video_canon_inputs"], {})
                self.assertEqual(result["promotion_commands"], [])

    def test_conflicting_ids_block_all_versions_and_exact_duplicates_dedupe(self):
        bundle = synthetic_bundle()
        bundle["teacher_assertions"] *= 2
        self.assertEqual(len(replay_result_bundle(bundle)["video_canon_inputs"]["video_canon_assertions"]), 1)
        bundle["teacher_assertions"][1] = deepcopy(bundle["teacher_assertions"][1])
        bundle["teacher_assertions"][1]["semantic_confidence"] = .98
        self.assertEqual(replay_result_bundle(bundle)["status"], "BLOCKED")

    def test_supplied_verification_is_never_used(self):
        bundle = synthetic_bundle()
        bundle["video_canon_verification_bundles"] = {"synthetic-assertion": {"status": "PASS"}}
        result = replay_result_bundle(bundle)
        self.assertEqual(result["promotion_commands"], [])
        self.assertEqual(result["video_canon_inputs"]["video_canon_verification_bundles"], {})

    def test_invalid_json_numbers_and_non_objects_rejected(self):
        for bundle in ({"value": float("nan")}, {"value": float("inf")}, []):
            with self.assertRaises(ValueError):
                replay_result_bundle(bundle)

    def test_sqlite_conflict_does_not_replace_existing_evidence(self):
        result = replay_result_bundle({})
        with sqlite3.connect(":memory:") as connection:
            stage_local(connection, result)
            bad = deepcopy(result)
            bad["candidates"][0]["payload"]["status"] = "tampered"
            with self.assertRaises(ValueError):
                stage_local(connection, bad)
            self.assertEqual(stage_local(connection, result)["existing"], 1)

    def test_quality_requested_analysis_persists_gap_instead_of_not_requested(self):
        from diana_longitudinal_quality_v4_2 import build_quality_layer
        from tests.test_diana_longitudinal_quality_v4_2 import base_master
        for extra in ({"video_canon_analysis_requested": True},
                      {"video_canon_assertions": []}):
            master = {**base_master(), **extra}
            quality = build_quality_layer(master)
            replay = quality["video_canon_auto_pipeline"]
            self.assertEqual(replay["status"], "BLOCKED")
            self.assertTrue(replay["gaps"])
            self.assertIn(replay["candidates"][0], quality["candidate_staging_records"])
            self.assertEqual(quality["counts"]["video_canon_auto_promotions_ready"], 0)
            self.assertEqual(quality["counts"]["video_canon_candidates"], 0)
            self.assertEqual(quality["counts"]["staging_records"], len(quality["candidate_staging_records"]))

    def test_quality_explicit_request_cannot_use_supplied_pass_bundle(self):
        from diana_longitudinal_quality_v4_2 import build_quality_layer
        from tests.test_diana_longitudinal_quality_v4_2 import base_master
        inputs = replay_result_bundle(synthetic_bundle())["video_canon_inputs"]
        inputs["video_canon_verification_bundles"] = {"synthetic-assertion": {"status": "PASS"}}
        quality = build_quality_layer({**base_master(), **inputs,
                                       "video_canon_analysis_requested": True})
        replay = quality["video_canon_auto_pipeline"]
        self.assertEqual(replay["status"], "BLOCKED")
        self.assertEqual(replay["video_canon_inputs"], {})
        self.assertEqual(replay["promotion_commands"], [])

    def test_cli_twice_preserves_source_and_deduplicates_disposable_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, database = root / "source.json", root / "local.sqlite"
            source.write_text(json.dumps(synthetic_bundle()), encoding="utf-8-sig")
            original = source.read_bytes()
            outputs = [root / "receipt-1.json", root / "receipt-2.json"]
            for output in outputs:
                command = [sys.executable, "-m", "tools.replay_video_canon", str(source),
                           "--output", str(output), "--local-staging-db", str(database)]
                subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            self.assertEqual(source.read_bytes(), original)
            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM replay_staging").fetchone()[0], 1)
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)

    def test_no_json_claim_can_replace_trusted_resolver(self):
        bundle = synthetic_bundle()
        bundle["trusted_manifest"] = simulated_resolver("c" * 64)
        bundle["evidence_resolver"] = "PASS"
        result = replay_untrusted(bundle)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["video_canon_inputs"], {})
        self.assertIn("TRUSTED_EVIDENCE_RESOLVER_MISSING", [g["code"] for g in result["gaps"]])

    def test_parent_teacher_frame_and_source_authority_fail_closed(self):
        mutations = [
            lambda m: m.update(parent_artifact_sha256="d" * 64),
            lambda m: m.update(parent_hash_domain="CANONICAL_JSON_SHA256"),
            lambda m: m.update(evidence_class="SYNTHETIC"),
            lambda m: m["source"].update(source_sha256="d" * 64),
            lambda m: m["transcripts"][0].update(speaker_role="learner"),
            lambda m: m["transcripts"][0].update(speaker_identity_status="UNKNOWN"),
            lambda m: m["transcripts"][0].update(text_sha256="e" * 64),
            lambda m: m["transcripts"][0].update(start=.1),
            lambda m: m["transcripts"][0]["frame_evidence"].update(frame_sha256="e" * 64),
            lambda m: m["transcripts"].append(deepcopy(m["transcripts"][0])),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                manifest = simulated_resolver("c" * 64)
                mutate(manifest)
                result = replay_untrusted(synthetic_bundle(), evidence_resolver=lambda _: manifest)
                self.assertEqual(result["status"], "BLOCKED")
                self.assertEqual(result["video_canon_inputs"], {})
                self.assertEqual(result["promotion_commands"], [])

    def test_resolver_error_and_empty_manifest_are_gaps(self):
        def broken(_):
            raise RuntimeError("private backend detail")
        for resolver in (broken, lambda _: None, lambda _: {}, lambda _: {"bad": object()}):
            result = replay_untrusted(synthetic_bundle(), evidence_resolver=resolver)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertNotIn("private backend detail", json.dumps(result))

    def test_normalized_ids_and_conflicting_aliases_cannot_silently_win(self):
        bundle = synthetic_bundle()
        duplicate = deepcopy(bundle["teacher_assertions"][0])
        duplicate.update(assertion_id=" synthetic-assertion ", semantic_confidence=.98)
        bundle["teacher_assertions"].append(duplicate)
        self.assertEqual(replay_result_bundle(bundle)["status"], "BLOCKED")
        bundle = synthetic_bundle()
        bundle["teacher_assertions"][0]["assertion_id"] = 123
        self.assertEqual(replay_result_bundle(bundle)["status"], "BLOCKED")
        bundle = synthetic_bundle()
        bundle["video_canon_assertions"] = []
        self.assertEqual(replay_result_bundle(bundle)["status"], "BLOCKED")

    def test_malformed_finite_json_produces_gaps(self):
        mutations = [
            lambda b: b["teacher_assertions"][0]["explanation"].update(why_or_purpose=[123]),
            lambda b: b["teacher_assertions"][0].update(source_class=[]),
            lambda b: b["learning_observation"]["transcript_evidence"][0].update(speaker_identity_status=[]),
        ]
        for mutate in mutations:
            bundle = synthetic_bundle()
            mutate(bundle)
            result = replay_result_bundle(bundle)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertTrue(result["gaps"])

    def test_each_missing_observation_field_is_precise(self):
        from bridge_contracts.video_canon_replay import OBSERVATION_FIELDS
        for field in OBSERVATION_FIELDS:
            bundle = synthetic_bundle()
            del bundle["learning_observation"][field]
            result = replay_result_bundle(bundle)
            self.assertIn("learning_observation." + field, [g["path"] for g in result["gaps"]])

    def test_provenance_receipt_is_durable_staging_payload(self):
        result = replay_result_bundle(synthetic_bundle())
        receipt = next(row for row in result["candidates"] if row["candidate_type"] == "video_canon_replay_receipt")
        binding = receipt["payload"]["provenance_bindings"][0]
        self.assertEqual(binding["parent_artifact_sha256"], "c" * 64)
        self.assertEqual(binding["candidate_payload_sha256"], result["candidates"][0]["payload_hash"])

    def test_self_claimed_source_approval_cannot_enter_verification(self):
        bundle = synthetic_bundle()
        assertion = bundle["teacher_assertions"][0]
        assertion["source_class"] = "SCHOOL_PRIMARY_EVIDENCE"
        assertion["source_authorization"] = {
            "status": "APPROVED", "decision_ref": "synthetic-decision",
            "policy_version": "synthetic-policy", "authorized_source_sha256": "a" * 64,
            "authorized_video_file_id": "synthetic-video",
            "authorized_teacher_ids": ["synthetic-teacher"],
            "approved_semantic_scopes": ["synthetic/scope"],
            "authorization_evidence_sha256": "e" * 64,
        }
        result = replay_result_bundle(bundle)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("SOURCE_AUTHORIZATION_UNPROVEN", [g["code"] for g in result["gaps"]])

    def test_parent_missing_invalid_hash_or_different_digest_domain_is_gap(self):
        for provenance in (None, {}, {"parent_artifact_sha256": "bad", "parent_hash_domain": "RAW_BYTES_SHA256"},
                {"parent_artifact_sha256": "c" * 64, "parent_hash_domain": "CANONICAL_JSON_SHA256"}):
            bundle = synthetic_bundle()
            bundle["replay_provenance"] = provenance
            self.assertEqual(replay_result_bundle(bundle)["status"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
