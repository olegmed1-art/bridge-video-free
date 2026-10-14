"""Offline orchestration checks; native SQL lineage remains in test_video_staging_postgres."""
from __future__ import annotations

import unittest

from database.video_staging_handoff import persist_quality_handoff
from video_completed_staging_handoff import stage_completed_result


def persisted():
    return {
        "persisted": True,
        "publication_authorization_status": "blocked",
        "outbox": {"publication_state": "BLOCKED_PENDING"},
        "changeset_id": "synthetic-change",
    }


def payload():
    return {
        "job_id": "synthetic-job",
        "quality_v2": {
            "candidate_staging_records": [{"candidate_type": "video_source_knowledge_draft"}],
            "video_canon_auto_pipeline": {"schema": "video-canon-staging-replay-v1"},
        },
    }


class VideoStagingHandoffTests(unittest.TestCase):
    def test_success_passes_actual_result_and_receipt(self):
        actual = persisted()
        calls = []

        def writer(dsn, quality, *, persisted_result):
            calls.append((dsn, quality, persisted_result))
            return {"status": "PERSISTED", "replay_receipt_id": "synthetic-receipt",
                    "candidate_records": 1, "inserted": 2}

        receipt = persist_quality_handoff("isolated-dsn", payload(), actual, writer=writer)
        self.assertEqual(receipt["replay_receipt_id"], "synthetic-receipt")
        self.assertIs(calls[0][2], actual)
        self.assertEqual(actual["outbox"]["publication_state"], "BLOCKED_PENDING")

    def test_repeat_reuses_one_candidate_and_receipt(self):
        rows = set()

        def writer(_dsn, _payload, *, persisted_result):
            self.assertIs(persisted_result, actual)
            before = len(rows)
            rows.add("synthetic-candidate")
            rows.add("synthetic-receipt")
            return {"status": "PERSISTED", "replay_receipt_id": "synthetic-receipt",
                    "candidate_records": 1, "inserted": len(rows) - before}

        actual = persisted()
        first = persist_quality_handoff("isolated-dsn", payload(), actual, writer=writer)
        repeat = persist_quality_handoff("isolated-dsn", payload(), actual, writer=writer)
        self.assertEqual((first["inserted"], repeat["inserted"], len(rows)), (2, 0, 2))

    def test_failure_between_result_and_candidates_retries(self):
        result = persisted()
        calls = 0

        def stage(*, persisted_result, staging_receipt):
            nonlocal calls
            self.assertIs(persisted_result, result)
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic pre-write failure")
            staging_receipt.update(status="PERSISTED", replay_receipt_id="receipt",
                                   candidate_records=1)
            return 0

        env = {"BRIDGE_PERSIST_DATABASE": "true", "BRIDGE_OUTPUT_FOLDER_ID": "synthetic-output",
               "BRIDGE_LESSON_NUMBER": "1"}
        with self.assertRaisesRegex(RuntimeError, "pre-write failure"):
            stage_completed_result(result, postprocess=stage, environment=env)
        done = stage_completed_result(result, postprocess=stage, environment=env)
        self.assertEqual((calls, done["knowledge_staging"]["replay_receipt_id"]), (2, "receipt"))

    def test_failure_after_receipt_retries_without_new_candidate(self):
        rows = set()
        attempts = 0

        def stage(*, persisted_result, staging_receipt):
            nonlocal attempts
            self.assertIs(persisted_result, actual)
            rows.update({"candidate", "receipt"})
            staging_receipt.update(status="PERSISTED", replay_receipt_id="receipt",
                                   candidate_records=1)
            attempts += 1
            if attempts == 1:
                raise RuntimeError("synthetic post-commit failure")
            return 0

        actual = persisted()
        env = {"BRIDGE_PERSIST_DATABASE": "true", "BRIDGE_OUTPUT_FOLDER_ID": "synthetic-output",
               "BRIDGE_LESSON_NUMBER": "1"}
        with self.assertRaisesRegex(RuntimeError, "post-commit failure"):
            stage_completed_result(actual, postprocess=stage, environment=env)
        done = stage_completed_result(actual, postprocess=stage, environment=env)
        self.assertEqual((attempts, len(rows), done["knowledge_staging"]["status"]),
                         (2, 2, "PERSISTED"))

    def test_mismatched_provenance_propagates_without_acknowledgement(self):
        def writer(_dsn, _payload, *, persisted_result):
            raise ValueError("CANDIDATE_SOURCE_HASH_MISMATCH")

        with self.assertRaisesRegex(ValueError, "CANDIDATE_SOURCE_HASH_MISMATCH"):
            persist_quality_handoff("isolated-dsn", payload(), persisted(), writer=writer)

    def test_empty_or_invalid_quality_never_calls_writer(self):
        def forbidden(*args, **kwargs):
            self.fail("writer was called")

        for invalid in ({}, {"job_id": "synthetic-job"},
                        {"job_id": "synthetic-job", "quality_v2": {}},
                        {"job_id": "synthetic-job", "quality_v2": {
                            "candidate_staging_records": [],
                            "video_canon_auto_pipeline": {"schema": "video-canon-staging-replay-v1"}}}):
            with self.subTest(payload=invalid), self.assertRaises(ValueError):
                persist_quality_handoff("isolated-dsn", invalid, persisted(), writer=forbidden)

    def test_non_replay_quality_is_explicitly_blocked(self):
        candidate = payload()
        candidate["quality_v2"]["candidate_staging_records"] = [
            {"candidate_type": "learning_evidence_chain"}]
        candidate["quality_v2"]["video_canon_auto_pipeline"] = {
            "schema": "video-canon-auto-pipeline-v1", "status": "NOT_REQUESTED"}
        result = persist_quality_handoff("isolated-dsn", candidate, persisted(),
                                         writer=lambda *_a, **_k: self.fail("writer was called"))
        self.assertEqual(result["status"], "BLOCKED_NO_REPLAY")

    def test_publication_boundary_cannot_be_bypassed(self):
        actual = persisted()
        actual["publication_authorization_status"] = "allowed"
        with self.assertRaisesRegex(ValueError, "PUBLICATION_BOUNDARY_CHANGED"):
            persist_quality_handoff("isolated-dsn", payload(), actual,
                                    writer=lambda *_a, **_k: self.fail("writer was called"))


if __name__ == "__main__":
    unittest.main()
