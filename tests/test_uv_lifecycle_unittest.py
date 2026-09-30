"""Synthetic local state tests; run with tools/run_uv_lifecycle_tests.py."""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

if not getattr(sys, "_uv_synthetic_guards_active", False):
    raise unittest.SkipTest("Run through tools/run_uv_lifecycle_tests.py with isolated guards")

from universal_video import runner, spool_worker
from universal_video.contract import canonical_job_hash, validate_job
from universal_video.lifecycle import (LifecycleError, archive_result, attempt_records, lifecycle_lock, read_json,
                                      start_attempt, finish_attempt)
from universal_video.server_intake import IntakeError, read_status, submit
from universal_video.result_conformance import verify_result
from universal_video.server_review import build_server_review
from universal_video.profiles import resolve_profile


def payload(job_id="fixture-job"):
    return {"job_id": job_id, "profile": "bridge_lesson", "source": {
        "kind": "google_drive", "file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz", "name": "fixture"},
        "metadata": {"purpose": "synthetic"}, "options": {"chunk_seconds": 300}}


def completed_bundle(root: Path, value: dict) -> dict:
    # Reuse only the existing fixture builder, not pytest or test execution.
    source = Path(__file__).with_name("test_universal_video_result_conformance.py")
    module = ast.parse(source.read_text())
    nodes = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name in {"_fingerprint", "_bundle"}]
    scope = {"hashlib": hashlib, "json": json, "Path": Path, "resolve_profile": resolve_profile}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), "exec"), scope)
    folder, manifest = scope["_bundle"](root)
    target = root / value["job_id"]
    folder.rename(target)
    manifest.update(job_id=value["job_id"], job_hash=canonical_job_hash(validate_job(value)))
    (target / "manifest.json").write_text(json.dumps(manifest))
    args = dict(expected_job_id=value["job_id"], expected_profile=value["profile"], expected_job_hash=manifest["job_hash"])
    base = verify_result(target, **args, evidence_phase="GENERATION_FINALIZATION")
    review = build_server_review(target, base)
    (target / "server_review.json").write_text(json.dumps(review))
    verify_result(target, **args, evidence_phase="GENERATION_FINALIZATION", require_server_review=True)
    return manifest


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spool = self.root / "spool"
        self.spool.mkdir()
        spool_worker._dirs(self.spool)
        self.staging = self.root / "staging"
        self.staging.mkdir()
        self.value = payload()
        self.job = validate_job(self.value)
        self.job_hash = canonical_job_hash(self.job)

    def tearDown(self):
        self.temp.cleanup()

    def submit(self, value=None):
        return submit(value or self.value, spool_root=self.spool, staging_root=self.staging)

    def test_identical_submit_is_idempotent_queued_running_failed(self):
        self.submit()
        self.assertEqual(self.submit(), self.job.job_id)
        self.assertEqual(read_status(self.job.job_id, spool_root=self.spool)["status"], "QUEUED")
        path = self.spool / "inbox" / f"{self.job.job_id}.json"
        path.rename(self.spool / "running" / path.name)
        self.assertEqual(self.submit(), self.job.job_id)
        self.assertEqual(read_status(self.job.job_id, spool_root=self.spool)["status"], "RUNNING")
        (self.spool / "running" / path.name).unlink()
        (self.spool / "failed" / path.name).write_text(json.dumps({"job_id": self.job.job_id,
            "job_hash": self.job_hash, "status": "FAILED", "error_code": "UV_TEST_FAILURE"}))
        self.assertEqual(self.submit(), self.job.job_id)
        self.assertEqual(read_status(self.job.job_id, spool_root=self.spool)["status"], "FAILED")
        self.assertFalse(list((self.spool / "inbox").iterdir()))

    def test_complete_same_request_reuses_verified_bundle(self):
        manifest = completed_bundle(self.spool / "results", self.value)
        (self.spool / "done" / f"{self.job.job_id}.json").write_text(json.dumps(manifest))
        with patch.object(spool_worker, "run_job", side_effect=AssertionError("double compute")):
            self.assertEqual(self.submit(), self.job.job_id)
            state = read_status(self.job.job_id, spool_root=self.spool)
            self.assertEqual(state["status"], "COMPLETED")
            self.assertEqual(state["result_conformance"]["state"], "PASS")
            self.assertEqual(state["pedagogical_status"], "NOT_EVALUATED")
            self.assertFalse(spool_worker.process_one(self.spool))

    def test_tampered_completed_artifact_fails_closed(self):
        manifest = completed_bundle(self.spool / "results", self.value)
        (self.spool / "done" / f"{self.job.job_id}.json").write_text(json.dumps(manifest))
        (self.spool / "results" / self.job.job_id / "transcript.txt").write_text("tampered")
        with self.assertRaises(IntakeError): self.submit()
        with self.assertRaises(IntakeError): read_status(self.job.job_id, spool_root=self.spool)

    def test_changed_metadata_options_or_source_conflict(self):
        self.submit()
        for key, value in (("metadata", {"purpose": "other"}), ("options", {"chunk_seconds": 600}),
                           ("source", {"kind": "google_drive", "file_id": "1OtherSource000000000000"})):
            other = copy.deepcopy(self.value); other[key] = value
            with self.assertRaises(IntakeError) as caught: self.submit(other)
            self.assertEqual(caught.exception.error_code, "UV_INTAKE_IDENTITY_CONFLICT")
        self.assertEqual(len(list((self.spool / "inbox").glob("*.json"))), 1)

    def test_concurrent_submit_has_one_request(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: self.submit(), range(24)))
        self.assertEqual(set(values), {self.job.job_id})
        self.assertEqual(len(list((self.spool / "inbox").glob("*.json"))), 1)

    def test_concurrent_claim_and_status_do_not_duplicate(self):
        self.submit(); entered = threading.Event(); release = threading.Event()
        def run(value, root):
            entered.set(); self.assertTrue(release.wait(5)); return self.review_result(value, root)
        with self.fake_worker(run):
            with ThreadPoolExecutor(max_workers=2) as pool:
                future = pool.submit(spool_worker.process_one, self.spool)
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.submit(), self.job.job_id)
                self.assertEqual(read_status(self.job.job_id, spool_root=self.spool)["status"], "RUNNING")
                release.set(); self.assertTrue(future.result(timeout=5))
        self.assertEqual(read_status(self.job.job_id, spool_root=self.spool)["status"], "REVIEW")
        self.assertEqual(len(attempt_records(self.spool, self.job.job_id)), 1)

    def review_result(self, value, root):
        folder = root / value["job_id"]; folder.mkdir(exist_ok=True)
        result = {"status": "REVIEW", "job_id": value["job_id"], "profile": value["profile"],
                  "job_hash": canonical_job_hash(validate_job(value)), "source": value["source"],
                  "deferred_analysis": ["bridge_positions", "educational_candidates"]}
        (folder / "manifest.json").write_text(json.dumps(result)); return result

    def fake_worker(self, run=None):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(spool_worker, "validate_video_runtime", return_value=None))
        stack.enter_context(patch.object(spool_worker, "stage_drive_job", side_effect=lambda job, value, root: (value, None)))
        stack.enter_context(patch.object(spool_worker, "validate_staged_video", return_value=None))
        stack.enter_context(patch.object(spool_worker, "run_job", side_effect=run or self.review_result))
        return stack

    def test_failed_compute_keeps_evidence_and_no_automatic_retry(self):
        self.submit()
        def failed(value, root):
            folder = root / value["job_id"]; folder.mkdir()
            (folder / "partial.txt").write_text("retain me")
            raise RuntimeError("synthetic failure")
        with self.fake_worker(failed): self.assertTrue(spool_worker.process_one(self.spool))
        self.assertEqual((self.spool / "results" / self.job.job_id / "partial.txt").read_text(), "retain me")
        self.assertEqual(attempt_records(self.spool, self.job.job_id)[0]["state"], "FAILED")
        self.assertEqual(self.submit(), self.job.job_id)
        with self.fake_worker(): self.assertFalse(spool_worker.process_one(self.spool))

    def test_forced_interrupt_then_one_restart_with_receipts(self):
        self.submit()
        def interrupted(value, root):
            folder = root / value["job_id"]; folder.mkdir()
            (folder / "partial.txt").write_text("interrupted evidence")
            raise KeyboardInterrupt()
        with self.fake_worker(interrupted):
            with self.assertRaises(KeyboardInterrupt): spool_worker.process_one(self.spool)
        counts = spool_worker.recover_orphaned_jobs(self.spool)
        self.assertEqual(counts["recovered"], 1)
        self.assertEqual(attempt_records(self.spool, self.job.job_id)[0]["state"], "INTERRUPTED")
        def restart(value, root):
            runner._prepare_job_dir(root, validate_job(value))
            return self.review_result(value, root)
        with self.fake_worker(restart): self.assertTrue(spool_worker.process_one(self.spool))
        saved = list((self.spool / "results" / ".attempts" / self.job.job_id).glob("attempt-*/partial.txt"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_text(), "interrupted evidence")
        self.assertEqual([r["state"] for r in attempt_records(self.spool, self.job.job_id)], ["INTERRUPTED", "REVIEW"])

    def test_second_interruption_exhausts_retry_budget(self):
        self.submit()
        with self.fake_worker(lambda value, root: (_ for _ in ()).throw(KeyboardInterrupt())):
            for index in range(2):
                with self.assertRaises(KeyboardInterrupt): spool_worker.process_one(self.spool)
                spool_worker.recover_orphaned_jobs(self.spool)
        status = read_status(self.job.job_id, spool_root=self.spool)
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["error_code"], "UV_RECOVERY_EXHAUSTED")
        with self.fake_worker(): self.assertFalse(spool_worker.process_one(self.spool))

    def test_terminal_ack_loss_does_not_rerun(self):
        self.submit()
        with self.fake_worker(): spool_worker.process_one(self.spool)
        path = self.spool / "running" / f"{self.job.job_id}.json"
        path.write_text(json.dumps(self.value))
        self.assertEqual(spool_worker.recover_orphaned_jobs(self.spool)["deduplicated"], 1)
        with self.fake_worker(): self.assertFalse(spool_worker.process_one(self.spool))

    def test_corrupt_receipt_and_payload_do_not_masquerade_as_success(self):
        for state in ("done", "failed", "running", "progress"):
            path = self.spool / state / f"{self.job.job_id}.json"
            path.write_text("{}")
            with self.assertRaises(IntakeError): self.submit()
            path.unlink()
        bad = payload("..");
        with self.assertRaises(IntakeError): self.submit(bad)

    def test_symlink_hardlink_and_parent_escape_rejected(self):
        original = self.root / "outside.json"; original.write_text(json.dumps(self.value))
        target = self.spool / "inbox" / f"{self.job.job_id}.json"
        for kind in ("symlink", "hardlink"):
            if kind == "symlink": target.symlink_to(original)
            else: os.link(original, target)
            with self.assertRaises(IntakeError): self.submit()
            target.unlink()
        (self.spool / "inbox").rmdir()
        (self.spool / "inbox").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(IntakeError): self.submit()
        self.assertEqual(json.loads(original.read_text()), self.value)

    def test_changed_processing_preserves_completed_generation(self):
        output = self.spool / "results"; folder = output / self.job.job_id; folder.mkdir()
        manifest = {"status": "COMPLETED", "job_hash": self.job_hash,
                    "source_fingerprint": "source", "processing_fingerprint": "old-model"}
        (folder / "manifest.json").write_text(json.dumps(manifest))
        (folder / "transcript.txt").write_text("original complete")
        _, _, reused = runner._prepare_job_dir(output, self.job, source_fingerprint="source",
            source_reuse_safe=True, processing_fingerprint="new-model")
        self.assertIsNone(reused)
        saved = list((output / ".attempts" / self.job.job_id).glob("attempt-*/transcript.txt"))
        self.assertEqual([p.read_text() for p in saved], ["original complete"])

    def test_storage_budget_never_deletes_evidence(self):
        output = self.spool / "results"; folder = output / self.job.job_id; folder.mkdir()
        (folder / "partial.txt").write_text("remain")
        with patch("universal_video.lifecycle.MAX_ARCHIVE_BYTES", 1):
            with self.assertRaises(LifecycleError): archive_result(output, self.job.job_id)
        self.assertEqual((folder / "partial.txt").read_text(), "remain")
        for _ in range(2):
            archive_result(output, self.job.job_id); folder.mkdir(); (folder / "partial.txt").write_text("remain")
        with self.assertRaises(LifecycleError): archive_result(output, self.job.job_id)
        self.assertEqual((folder / "partial.txt").read_text(), "remain")

    def test_inherited_queue_configuration_rejected_without_reading_secret(self):
        for key in ("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", "BRIDGE_WORKER_DATABASE_URL"):
            with patch.dict(os.environ, {key: "synthetic-forbidden"}):
                with self.assertRaises(LifecycleError): spool_worker.process_one(self.spool)
                with self.assertRaises(LifecycleError): spool_worker.run_forever(self.spool, 1)
        self.assertFalse(spool_worker.process_one(self.spool))

    def test_network_guard_is_active(self):
        with self.assertRaises(AssertionError): socket.socket()

    def test_truncated_manifest_is_archived_before_restart(self):
        output = self.spool / "results"; folder = output / self.job.job_id; folder.mkdir()
        (folder / "manifest.json").write_text('{"status":')
        (folder / "transcript.txt").write_text("partial exact bytes")
        runner._prepare_job_dir(output, self.job)
        old = list((output / ".attempts" / self.job.job_id).glob("attempt-*/manifest.json"))
        self.assertEqual([p.read_text() for p in old], ['{"status":'])

    def test_malformed_orphan_does_not_block_unrelated_recovery(self):
        bad = self.spool / "running" / "a-bad.json"; bad.write_text("{truncated")
        good = payload("z-good")
        (self.spool / "running" / "z-good.json").write_text(json.dumps(good))
        counts = spool_worker.recover_orphaned_jobs(self.spool)
        self.assertEqual(counts["rejected"], 1)
        self.assertEqual(counts["recovered"], 1)
        self.assertTrue((self.spool / "inbox" / "z-good.json").exists())
        saved = list((self.spool / "attempts").glob("a-bad.*.running.quarantined"))
        self.assertEqual([p.read_text() for p in saved], ["{truncated"])

    def test_link_publication_interruption_requires_exact_submit_retry(self):
        with patch("universal_video.server_intake._unlink_staged", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): self.submit()
        target = self.spool / "inbox" / f"{self.job.job_id}.json"
        self.assertEqual(target.stat().st_nlink, 2)
        with self.fake_worker(): self.assertFalse(spool_worker.process_one(self.spool))
        self.assertTrue(target.exists())
        # A different key is still eligible; the blocked payload does not starve it.
        other = payload("another-job"); self.submit(other)
        with self.fake_worker(): self.assertTrue(spool_worker.process_one(self.spool))
        self.assertEqual(self.submit(), self.job.job_id)
        self.assertEqual(target.stat().st_nlink, 1)
        self.assertFalse(list(self.staging.iterdir()))
        with self.fake_worker(): self.assertTrue(spool_worker.process_one(self.spool))
        self.assertEqual(len(attempt_records(self.spool, self.job.job_id)), 1)

    def test_attempt_without_request_is_not_not_found(self):
        with lifecycle_lock(self.spool): start_attempt(self.spool, self.job.job_id, self.job_hash)
        with self.assertRaises(IntakeError): read_status(self.job.job_id, spool_root=self.spool)
        with self.assertRaises(IntakeError): self.submit()

    def test_terminal_attempt_without_receipt_never_requeues(self):
        path = self.spool / "running" / f"{self.job.job_id}.json"; path.write_text(json.dumps(self.value))
        with lifecycle_lock(self.spool):
            index = start_attempt(self.spool, self.job.job_id, self.job_hash)
            finish_attempt(self.spool, self.job.job_id, index, "REVIEW")
        result = spool_worker.recover_orphaned_jobs(self.spool)
        self.assertEqual(result["rejected"], 1)
        self.assertFalse(list((self.spool / "inbox").glob("*.json")))

    def test_terminal_plus_duplicate_inbox_never_double_computes(self):
        self.submit()
        with self.fake_worker(): self.assertTrue(spool_worker.process_one(self.spool))
        (self.spool / "inbox" / f"{self.job.job_id}.json").write_text(json.dumps(self.value))
        with self.fake_worker(lambda *args: self.fail("duplicate compute")):
            self.assertTrue(spool_worker.process_one(self.spool))
            self.assertFalse(spool_worker.process_one(self.spool))
        self.assertEqual(len(attempt_records(self.spool, self.job.job_id)), 1)

    def test_lifecycle_lock_uses_installed_inode_under_restrictive_umask(self):
        # No lock-file initialization, ownership mutation or FIFO open occurs.
        lock = self.spool / ".lifecycle.lock"
        os.mkfifo(lock)
        before = self.spool.stat()
        old = os.umask(0o077)
        try:
            with patch("os.fchown", side_effect=AssertionError("unexpected ownership change")):
                with lifecycle_lock(self.spool): pass
        finally:
            os.umask(old)
        after = self.spool.stat()
        self.assertEqual((before.st_ino, before.st_gid, before.st_mode), (after.st_ino, after.st_gid, after.st_mode))
        import stat
        self.assertTrue(stat.S_ISFIFO(lock.lstat().st_mode))


    def test_reserved_archive_namespace_is_rejected(self):
        with self.assertRaises(IntakeError): self.submit(payload(".attempts"))

    def test_preserve_intent_interrupt_is_explicit_block_with_bytes_retained(self):
        output = self.spool / "results"; folder = output / self.job.job_id; folder.mkdir()
        (folder / "partial.txt").write_text("retain transaction")
        original = Path.rename
        def interrupted(path, target):
            if path == folder: raise KeyboardInterrupt()
            return original(path, target)
        with patch.object(Path, "rename", interrupted):
            with self.assertRaises(KeyboardInterrupt): archive_result(output, self.job.job_id)
        with self.assertRaises(LifecycleError): runner._prepare_job_dir(output, self.job)
        self.assertEqual((folder / "partial.txt").read_text(), "retain transaction")
        intents = list((output / ".attempts" / self.job.job_id).glob("*.json"))
        self.assertEqual(len(intents), 1)
        self.assertEqual(read_json(intents[0])["state"], "PRESERVE_INTENT")

    def test_conflicting_orphans_preserve_both_and_never_run(self):
        other = copy.deepcopy(self.value); other["options"]["chunk_seconds"] = 600
        filename = f"{self.job.job_id}.json"
        (self.spool / "inbox" / filename).write_text(json.dumps(other))
        (self.spool / "running" / filename).write_text(json.dumps(self.value))
        self.assertEqual(spool_worker.recover_orphaned_jobs(self.spool)["conflicts"], 1)
        self.assertFalse(list((self.spool / "inbox").iterdir()))
        self.assertEqual(len(list((self.spool / "attempts").glob("*.receipt.json"))), 1)
        with self.fake_worker(): self.assertFalse(spool_worker.process_one(self.spool))

    def test_empty_spool_recovers_without_missing_directory(self):
        empty = self.root / "empty-spool"; empty.mkdir()
        self.assertEqual(spool_worker.recover_orphaned_jobs(empty),
                         {"recovered": 0, "deduplicated": 0, "conflicts": 0, "rejected": 0})

    def test_quarantine_tombstone_interruption_blocks_job_id_reuse(self):
        filename = f"{self.job.job_id}.json"
        path = self.spool / "running" / filename; path.write_text("{broken")
        def failed_write(*args, **kwargs): raise KeyboardInterrupt()
        with patch.object(spool_worker, "_atomic_write_json", failed_write):
            with self.assertRaises(KeyboardInterrupt): spool_worker.recover_orphaned_jobs(self.spool)
        self.assertFalse(path.exists())
        with self.assertRaises(IntakeError): self.submit()
        with self.assertRaises(IntakeError): read_status(self.job.job_id, spool_root=self.spool)
        self.assertEqual(len(list((self.spool / "attempts").glob("*.quarantined"))), 1)
