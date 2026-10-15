#!/usr/bin/env python3
"""Bounded producer-to-outbox decision tests, without a database."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import Mock, patch


MODULE = pathlib.Path(__file__).resolve().parents[1] / "database" / "outbox_publication_decision.py"
spec = importlib.util.spec_from_file_location("outbox_publication_decision", MODULE)
assert spec and spec.loader
decision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decision)


class OutboxPublicationDecisionTests(unittest.TestCase):
    def test_blocked_result_stays_pending_without_publisher_call(self):
        publish = Mock()
        result = decision.publish_if_authorized(
            "unused", {"changeset_id": "change-1", "publication_authorization_status": "blocked"}, publish
        )
        publish.assert_not_called()
        self.assertEqual(result["publication_state"], "BLOCKED_PENDING")
        self.assertEqual(result["published_count"], 0)

    def test_unknown_null_missing_and_other_values_stay_pending(self):
        for payload in ({}, {"publication_authorization_status": None},
                        {"publication_authorization_status": "unknown"},
                        {"publication_authorization_status": "revoked"},
                        {"publication_authorization_status": "Blocked"}):
            with self.subTest(payload=payload):
                publish = Mock()
                result = decision.publish_if_authorized(
                    "unused", {"changeset_id": "change-2", **payload}, publish
                )
                publish.assert_not_called()
                self.assertEqual(result["publication_state"], "UNKNOWN_PENDING")

    def test_explicit_allowed_delegates_once_and_preserves_positions(self):
        publish = Mock(return_value={"changeset_id": "change-3", "published_count": 2,
                                     "event_positions": [4, 5]})
        result = decision.publish_if_authorized(
            "dsn", {"changeset_id": "change-3", "publication_authorization_status": "allowed"}, publish
        )
        publish.assert_called_once_with("dsn", "change-3")
        self.assertEqual(result["event_positions"], [4, 5])
        self.assertEqual(result["publication_state"], "PUBLISHED")

    def test_allowed_changeset_with_no_events_does_not_claim_publication(self):
        publish = Mock(return_value={"changeset_id": "change-3", "published_count": 0,
                                     "event_positions": []})
        result = decision.publish_if_authorized(
            "dsn", {"changeset_id": "change-3", "publication_authorization_status": "allowed"}, publish
        )
        publish.assert_called_once_with("dsn", "change-3")
        self.assertEqual(result["publication_state"], "NO_EVENTS")
        self.assertEqual(result["published_count"], 0)

    def test_inconsistent_publisher_result_fails_closed(self):
        publish = Mock(return_value={"changeset_id": "change-3", "published_count": 1,
                                     "event_positions": []})
        with self.assertRaisesRegex(RuntimeError, "OUTBOX_PUBLICATION_RESULT_INVALID"):
            decision.publish_if_authorized(
                "dsn", {"changeset_id": "change-3", "publication_authorization_status": "allowed"}, publish
            )

    def test_guard_error_is_not_swallowed(self):
        publish = Mock(side_effect=RuntimeError("OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED"))
        with self.assertRaisesRegex(RuntimeError, "OUTBOX_PUBLICATION_AUTHORIZATION_BLOCKED"):
            decision.publish_if_authorized(
                "dsn", {"changeset_id": "change-4", "publication_authorization_status": "allowed"}, publish
            )

    def test_existing_bridge_persistence_route_records_blocked_pending(self):
        root = MODULE.parents[1]
        database = types.ModuleType("database")
        database.__path__ = [str(root / "database")]
        io = types.ModuleType("run_drive_3_1_free")
        io.safe = Mock()
        publisher = types.ModuleType("database.outbox_publisher")
        publisher.publish_changeset_outbox = Mock()
        checkpoint = types.ModuleType("database.run_checkpoint_persistence")
        checkpoint.record_checkpoint = Mock(return_value={"checkpoint": "recorded"})
        identity = types.ModuleType("database.source_identity_persistence")
        identity.ensure_drive_source_identity = Mock(return_value={"source_identity_id": "source-1"})
        producer = types.ModuleType("database.video_result_persistence")
        producer.persist_video_result = Mock(return_value={
            "changeset_id": "change-5", "publication_authorization_status": "blocked"})
        stubs = {
            "database": database, "run_drive_3_1_free": io,
            "database.outbox_publisher": publisher,
            "database.run_checkpoint_persistence": checkpoint,
            "database.source_identity_persistence": identity,
            "database.video_result_persistence": producer,
        }
        with patch.dict(sys.modules, stubs), patch.dict(os.environ, {
            "BRIDGE_WORKER_DATABASE_URL": "synthetic-dsn", "BRIDGE_JOB_ID": "job-1"
        }, clear=True):
            source = root / "bridge_neon_persistence.py"
            spec = importlib.util.spec_from_file_location("synthetic_bridge_persistence", source)
            assert spec and spec.loader
            persistence = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(persistence)
            persistence._load_done = Mock(return_value={"job_id": "job-1"})
            persistence._load_embedded_master = Mock(return_value={
                "job_id": "job-1", "source": {"driveId": "drive-1"}})
            result = persistence.persist_completed_drive_job("synthetic-token")

        publisher.publish_changeset_outbox.assert_not_called()
        self.assertEqual(result["outbox"]["publication_state"], "BLOCKED_PENDING")
        self.assertEqual(result["outbox"]["published_count"], 0)
        self.assertEqual(checkpoint.record_checkpoint.call_args.kwargs["details"]
                         ["outbox_publication_state"], "BLOCKED_PENDING")
        self.assertEqual(io.safe.call_args.kwargs["outbox_publication_state"], "BLOCKED_PENDING")

    def test_optional_workflow_preflight_binds_entire_publication_path(self):
        root = MODULE.parents[1]
        workflow = (root / ".github/workflows/bridge-video-3.1-free.yml").read_text()
        section = workflow.split("- name: Verify guarded publisher source for optional persistence", 1)[1]
        section = section.split("- name: Deterministic source identity preflight", 1)[0]
        self.assertNotIn("if: env.BRIDGE_PERSIST_DATABASE == 'true'", section)
        self.assertIn("id: outbox_source", section)
        self.assertIn("steps.outbox_source.outcome == 'success'", workflow)
        script = section.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        script = textwrap.dedent(script)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            paths = (
                "database/outbox_publisher.py", "database/outbox_publication_decision.py",
                "bridge_neon_persistence.py", "database/backfill_video_results.py",
                "database/video_result_persistence.py", "database/source_registration.py",
                "database/source_identity_persistence.py", "database/runtime_worker_preflight.py",
                "run_master_3_1_free_semantic_v2.py", "run_master_3_1_free_semantic.py",
            )
            for name in paths:
                dest = pathlib.Path(temp) / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / name, dest)
            ok = subprocess.run([sys.executable, "-B", "-c", script], cwd=temp,
                                capture_output=True, text=True, check=False)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("OUTBOX_PUBLICATION_SOURCE_BOUND", ok.stdout)
            for name in ("database/outbox_publisher.py", "database/outbox_publication_decision.py"):
                with self.subTest(modified=name):
                    dest = pathlib.Path(temp) / name
                    original = dest.read_bytes()
                    dest.write_bytes(original + b"# changed\n")
                    refused = subprocess.run([sys.executable, "-B", "-c", script], cwd=temp,
                                             capture_output=True, text=True, check=False)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn("OUTBOX_PUBLICATION_SOURCE_MISMATCH: " + name, refused.stderr)
                    dest.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
