"""Synthetic combined-queue boundary checks; no database or Drive access."""
from __future__ import annotations

import importlib
import hashlib
import json
import os
import sys
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch


# The worker imports optional platform/transport modules at import time. Its
# external calls are patched below; no dependency is installed for this test.
for optional in ("requests", "fcntl"):
    try:
        importlib.import_module(optional)
    except ModuleNotFoundError:
        sys.modules[optional] = types.ModuleType(optional)

from universal_video import neon_worker
from universal_video.queue_staging_handoff import ExactLeaseStagingHandoff
from database.video_staging_receipt_read import read_committed_receipt_on_connection


def claim():
    return {
        "batch_id": "batch-synthetic", "job_id": "queue-job-synthetic",
        "lease_token": "lease-synthetic", "stable_job_key": "bridge-job-synthetic",
        "source_file_id": "driveVideoSynthetic", "algorithm_revision": "3.1-free-r25.16",
        "output_folder_id": "outputSynthetic", "work_folder_id": "workSynthetic",
        "sequence": 1,
    }


def producer_result(*, staging_status="PERSISTED", receipt_id=None):
    source_id = "11111111-1111-4111-8111-111111111111"
    return {
        "persisted": True,
        "source_id": source_id,
        "analysis_run_id": "22222222-2222-4222-8222-222222222222",
        "publication_authorization_status": "blocked",
        "source_identity": {"source_id": source_id,
                            "source_native_key": "google-drive:driveVideoSynthetic"},
        "outbox": {"publication_state": "BLOCKED_PENDING"},
        "checkpoint": {"stage_key": "database_persist", "checkpoint_state": "completed"},
        "knowledge_staging": {"status": staging_status,
                              "replay_receipt_id": receipt_id,
                              "candidate_records": 1},
    }


RECEIPT_ID = "33333333-3333-4333-8333-333333333333"


class CombinedQueueStagingTests(unittest.TestCase):
    def _run(self, handoff, *, processor=None, source_after_staging=None):
        observed = {"finish": [], "retry": [], "events": []}
        sources = iter(({"revision": "same"}, {"revision": "same"},
                        source_after_staging or {"revision": "same"}))

        def persist_queue_finish(_dsn, **kwargs):
            observed["events"].append("finish")
            observed["finish"].append(kwargs)
            return {"job_status": kwargs["outcome"]}

        def retry(_dsn, **kwargs):
            observed["events"].append("retry")
            observed["retry"].append(kwargs)
            return {"job_status": "QUEUED"}

        with patch.object(neon_worker, "_Heartbeat", lambda *_a: nullcontext(types.SimpleNamespace(error=None))), \
             patch.object(neon_worker, "_processing_timeout", nullcontext), \
             patch.object(neon_worker, "access_token", lambda: "synthetic-token"), \
             patch.object(neon_worker, "verify_claimed_source", lambda *_a: next(sources)), \
             patch.object(neon_worker, "verify_terminal_output_live", lambda *_a: None), \
             patch.object(neon_worker, "finish_job", persist_queue_finish), \
             patch.object(neon_worker, "retry_job", retry):
            result = neon_worker.process_claim(
                "synthetic-queue-dsn", claim(), "worker-synthetic",
                processor=processor or (lambda _claim: {"terminal": "synthetic"}),
                staging_handoff=handoff,
            )
        return result, observed

    def test_finish_requires_exact_committed_receipt_and_scoped_writer(self):
        seen = []
        committed = {}

        def persist(token):
            seen.append((token, os.environ["BRIDGE_JOB_ID"],
                         os.environ["BRIDGE_PERSIST_DATABASE"],
                         os.environ["BRIDGE_WORKER_DATABASE_URL"]))
            committed[RECEIPT_ID] = {
                "status": "COMMITTED_RECEIPT", "receipt_id": RECEIPT_ID,
                "analysis_run_id": "22222222-2222-4222-8222-222222222222",
                "source_id": "11111111-1111-4111-8111-111111111111",
            }
            return producer_result(receipt_id=RECEIPT_ID)

        authorization = ExactLeaseStagingHandoff(
            claim=claim(), worker_key="worker-synthetic", writer_dsn="synthetic-writer-dsn",
            persist_completed=persist,
            read_committed_receipt=lambda **fields: committed.get(fields["receipt_id"]))
        prior = os.environ.get("BRIDGE_WORKER_DATABASE_URL")
        result, observed = self._run(
            authorization,
            processor=lambda _: {"knowledge_staging": {"status": "PERSISTED",
                                                       "replay_receipt_id": "forged"}},
        )
        self.assertEqual(result["job_status"], "REVIEW_READY")
        self.assertEqual(observed["events"], ["finish"])
        output = observed["finish"][0]["output"]
        self.assertEqual(output["knowledge_staging"]["replay_receipt_id"], RECEIPT_ID)
        self.assertEqual(output["knowledge_staging"]["authorization_scope"], "EXACT_CLAIM_LEASE")
        self.assertIs(output["database_persistence_allowed"], False)
        self.assertEqual(seen, [("synthetic-token", "bridge-job-synthetic", "true",
                                 "synthetic-writer-dsn")])
        self.assertEqual(os.environ.get("BRIDGE_WORKER_DATABASE_URL"), prior)

    def test_no_receipt_retries_without_finish(self):
        authorization = ExactLeaseStagingHandoff(
            claim=claim(), worker_key="worker-synthetic", writer_dsn="synthetic-writer-dsn",
            persist_completed=lambda _token: producer_result(staging_status="BLOCKED_NO_REPLAY"),
            read_committed_receipt=lambda **_fields: self.fail("readback was called"))
        result, observed = self._run(authorization)
        self.assertEqual(result["job_status"], "QUEUED")
        self.assertEqual(observed["events"], ["retry"])
        self.assertEqual(observed["retry"][0]["error_code"], "UV_STAGING_RECEIPT_MISSING")

    def test_wrong_lease_never_calls_writer_or_finishes(self):
        called = []
        other = claim() | {"lease_token": "different-lease"}
        authorization = ExactLeaseStagingHandoff(
            claim=other, worker_key="worker-synthetic", writer_dsn="synthetic-writer-dsn",
            persist_completed=lambda _token: called.append(True),
            read_committed_receipt=lambda **_fields: self.fail("readback was called"))
        result, observed = self._run(authorization)
        self.assertEqual(result["job_status"], "QUEUED")
        self.assertEqual(observed["events"], ["retry"])
        self.assertEqual(called, [])

    def test_writer_failure_restores_environment_and_never_finishes(self):
        def failure(_token):
            raise RuntimeError("synthetic writer interruption")

        authorization = ExactLeaseStagingHandoff(
            claim=claim(), worker_key="worker-synthetic", writer_dsn="synthetic-writer-dsn",
            persist_completed=failure,
            read_committed_receipt=lambda **_fields: self.fail("readback was called"))
        prior = os.environ.get("BRIDGE_PERSIST_DATABASE")
        result, observed = self._run(authorization)
        self.assertEqual(result["job_status"], "QUEUED")
        self.assertEqual(observed["events"], ["retry"])
        self.assertEqual(os.environ.get("BRIDGE_PERSIST_DATABASE"), prior)

    def test_uncommitted_receipt_readback_blocks_finish_after_writer_return(self):
        authorization = ExactLeaseStagingHandoff(
            claim=claim(), worker_key="worker-synthetic", writer_dsn="synthetic-writer-dsn",
            persist_completed=lambda _token: producer_result(receipt_id=RECEIPT_ID),
            read_committed_receipt=lambda **_fields: None)
        result, observed = self._run(authorization)
        self.assertEqual(result["job_status"], "QUEUED")
        self.assertEqual(observed["events"], ["retry"])
        self.assertEqual(observed["retry"][0]["error_code"], "UV_STAGING_RECEIPT_MISSING")

    def test_source_change_after_committed_receipt_retries_without_finish(self):
        witness = {"status": "COMMITTED_RECEIPT", "receipt_id": RECEIPT_ID,
                   "analysis_run_id": "22222222-2222-4222-8222-222222222222",
                   "source_id": "11111111-1111-4111-8111-111111111111"}
        authorization = ExactLeaseStagingHandoff(
            claim=claim(), worker_key="worker-synthetic", writer_dsn="synthetic-writer-dsn",
            persist_completed=lambda _token: producer_result(receipt_id=RECEIPT_ID),
            read_committed_receipt=lambda **_fields: witness)
        result, observed = self._run(authorization, source_after_staging={"revision": "changed"})
        self.assertEqual(result["job_status"], "QUEUED")
        self.assertEqual(observed["events"], ["retry"])

    def test_queue_credential_cannot_be_reused_as_staging_writer(self):
        authorization = ExactLeaseStagingHandoff(
            claim=claim(), worker_key="worker-synthetic", writer_dsn="synthetic-queue-dsn",
            persist_completed=lambda _token: self.fail("writer was called"),
            read_committed_receipt=lambda **_fields: self.fail("readback was called"))
        with self.assertRaisesRegex(neon_worker.NeonVideoWorkerError,
                                    "VIDEO_STAGING_SEPARATE_WRITER_REQUIRED"):
            self._run(authorization)

    def test_readback_checks_receipt_hash_and_read_only_transaction(self):
        payload = {"schema": "video-staging-replay-snapshot-v1",
                   "binding": {"analysis_run_id": "22222222-2222-4222-8222-222222222222",
                               "source_id": "11111111-1111-4111-8111-111111111111"},
                   "members": [{"analysis_candidate_id": "synthetic-member"}]}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                           separators=(",", ":"), allow_nan=False).encode()).hexdigest()

        class Cursor:
            def __init__(self, *, readonly=True, stored_hash=digest):
                self.readonly, self.stored_hash, self.query = readonly, stored_hash, ""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query, _params=None):
                self.query = query

            def fetchone(self):
                return ("on" if self.readonly else "off",) if "transaction_read_only" in self.query else ("repeatable read",)

            def fetchall(self):
                return [(RECEIPT_ID, "22222222-2222-4222-8222-222222222222",
                         "11111111-1111-4111-8111-111111111111", payload,
                         self.stored_hash, digest, "video-staging-replay-snapshot-v1")]

        class Connection:
            def __init__(self, cursor):
                self._cursor = cursor

            def transaction(self):
                return nullcontext()

            def cursor(self):
                return self._cursor

        args = dict(receipt_id=RECEIPT_ID,
                    analysis_run_id="22222222-2222-4222-8222-222222222222",
                    source_id="11111111-1111-4111-8111-111111111111")
        with patch.dict(sys.modules, {"database.video_result_persistence":
                                      types.SimpleNamespace(SCHOOL_STABLE_NAME="synthetic-school")}):
            witness = read_committed_receipt_on_connection(Connection(Cursor()), **args)
            self.assertEqual(witness["status"], "COMMITTED_RECEIPT")
            with self.assertRaisesRegex(ValueError, "READ_ONLY_REQUIRED"):
                read_committed_receipt_on_connection(Connection(Cursor(readonly=False)), **args)
            with self.assertRaisesRegex(ValueError, "HASH_MISMATCH"):
                read_committed_receipt_on_connection(Connection(Cursor(stored_hash="invalid")), **args)


if __name__ == "__main__":
    unittest.main()
