"""Offline checks of the exact r26 semantic-v2 replay persistence branch."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def _load_function(path: Path, name: str, namespace: dict) -> object:
    source = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


class SemanticV2PersistenceGateTests(unittest.TestCase):
    def _route(self, flag: str | None, *, existing: bool = True, mode: str = "explicit_v1"):
        policy = _load_function(
            ROOT / "run_master_3_1_free_semantic.py", "_database_persistence_enabled", {"os": os}
        )
        safe = Mock()
        persist = Mock()
        heavy = Mock(side_effect=AssertionError("HEAVY_REPROCESSING_FORBIDDEN")) if existing else Mock(return_value={"status": "AI_DONE"})
        namespace = {
            "os": os,
            "_existing_same_revision_done": Mock(return_value={"status": "AI_DONE"} if existing else None),
            "base": SimpleNamespace(io=SimpleNamespace(safe=safe)),
            "previous": SimpleNamespace(_database_persistence_enabled=policy, process_job=heavy),
            "persist_completed_drive_job": persist,
        }
        process = _load_function(ROOT / "run_master_3_1_free_semantic_v2.py", "process_job", namespace)
        environment = {
            "BRIDGE_JOB_ID": "synthetic-job", "BRIDGE_PERSISTENCE_MODE": mode,
            "BRIDGE_WORKER_DATABASE_URL": "synthetic-dsn",
        }
        if flag is not None:
            environment["BRIDGE_PERSIST_DATABASE"] = flag
        with patch.dict(os.environ, environment, clear=True):
            result = process("synthetic-token")
        self.assertEqual(result, {"status": "AI_DONE"})
        if existing:
            safe.assert_called_once()
        else:
            safe.assert_not_called()
        if existing:
            heavy.assert_not_called()
        else:
            heavy.assert_called_once_with("synthetic-token")
        return persist

    def test_replay_with_explicit_false_and_db_url_does_not_persist(self):
        self._route("false").assert_not_called()

    def test_replay_with_explicit_true_delegates_once(self):
        self._route("true").assert_called_once_with("synthetic-token")

    def test_new_mode_fails_closed_on_missing_and_invalid_flags(self):
        for flag in (None, "", "false", "unexpected"):
            with self.subTest(flag=flag):
                self._route(flag).assert_not_called()
                self._route(flag, existing=False).assert_not_called()

    def test_new_mode_true_routes_fresh_once(self):
        # Execute v2 -> previous, the same two-layer route as the worker entrypoint.
        policy = _load_function(ROOT / "run_master_3_1_free_semantic.py", "_database_persistence_enabled", {"os": os})
        persist = Mock()
        previous_namespace = {
            "os": os, "_existing_same_revision_done": Mock(return_value=None),
            "base": SimpleNamespace(process_job=Mock(return_value={"status": "AI_DONE"})),
            "_database_persistence_enabled": policy, "persist_completed_drive_job": persist,
        }
        previous_process = _load_function(ROOT / "run_master_3_1_free_semantic.py", "process_job", previous_namespace)
        v2_namespace = {
            "os": os, "_existing_same_revision_done": Mock(return_value=None),
            "previous": SimpleNamespace(process_job=previous_process),
        }
        v2_process = _load_function(ROOT / "run_master_3_1_free_semantic_v2.py", "process_job", v2_namespace)
        with patch.dict(os.environ, {"BRIDGE_JOB_ID": "synthetic-job", "BRIDGE_PERSISTENCE_MODE": "explicit_v1",
                                     "BRIDGE_PERSIST_DATABASE": "true", "BRIDGE_WORKER_DATABASE_URL": "synthetic-dsn"}, clear=True):
            self.assertEqual(v2_process("synthetic-token"), {"status": "AI_DONE"})
        previous_namespace["base"].process_job.assert_called_once_with("synthetic-token")
        persist.assert_called_once_with("synthetic-token")

    def test_legacy_missing_flag_remains_enabled(self):
        policy = _load_function(ROOT / "run_master_3_1_free_semantic.py", "_database_persistence_enabled", {"os": os})
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(policy())
        with patch.dict(os.environ, {"BRIDGE_PERSISTENCE_MODE": "legacy"}, clear=True):
            self.assertTrue(policy())

    def test_unknown_nonempty_mode_is_rejected_before_persistence(self):
        for mode in ("explict_v1", "future_mode"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(ValueError, "UNKNOWN_BRIDGE_PERSISTENCE_MODE"):
                    self._route(None, mode=mode)
                with self.assertRaisesRegex(ValueError, "UNKNOWN_BRIDGE_PERSISTENCE_MODE"):
                    self._route("true", mode=mode)

    def test_workflow_preflight_binds_replay_gate_and_denies_modified_source(self):
        workflow = (ROOT / ".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
        section = workflow.split("- name: Verify guarded publisher source for optional persistence", 1)[1]
        section = section.split("- name: Deterministic source identity preflight", 1)[0]
        self.assertNotIn("if: env.BRIDGE_PERSIST_DATABASE == 'true'", section)
        script = textwrap.dedent(section.split("python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0])
        paths = (
            "database/outbox_publisher.py", "database/outbox_publication_decision.py",
            "bridge_neon_persistence.py", "video_completed_staging_handoff.py",
            "database/video_staging_handoff.py", "database/video_candidate_persistence.py",
            "diana_longitudinal_postprocess_v3.py",
            "diana_longitudinal_postprocess_v4_2.py",
            "database/backfill_video_results.py",
            "database/video_result_persistence.py", "database/runtime_worker_preflight.py",
            "run_master_3_1_free_semantic_v2.py", "run_master_3_1_free_semantic.py",
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            for name in paths:
                dest = Path(temp) / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, dest)
            ok = subprocess.run([sys.executable, "-B", "-c", script], cwd=temp,
                                capture_output=True, text=True, check=False)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            source = Path(temp) / "run_master_3_1_free_semantic_v2.py"
            source.write_bytes(source.read_bytes() + b"# tampered\n")
            refused = subprocess.run([sys.executable, "-B", "-c", script], cwd=temp,
                                     capture_output=True, text=True, check=False)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("OUTBOX_PUBLICATION_SOURCE_MISMATCH: run_master_3_1_free_semantic_v2.py", refused.stderr)

    def test_workflow_routes_false_missing_true_and_terminal_once(self):
        workflow = (ROOT / ".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
        def step(name: str) -> str:
            return workflow.split(f"- name: {name}\n", 1)[1].split("\n      - name: ", 1)[0]
        self.assertIn("BRIDGE_PERSISTENCE_MODE: explicit_v1", workflow)
        self.assertIn('PERSIST_DATABASE="false"', workflow)
        self.assertIn("env.BRIDGE_PERSIST_DATABASE == 'true'", step("Record durable worker start checkpoint"))
        process = step("Process one opaque Drive job with heartbeat")
        self.assertIn("env.BRIDGE_PERSIST_DATABASE == 'true' && secrets.BRIDGE_WORKER_DATABASE_URL || ''", process)
        self.assertIn('if [ "$BRIDGE_PERSIST_DATABASE" = "true" ]; then', process)
        self.assertIn('if [ "$BRIDGE_PERSIST_DATABASE" != "true" ]; then\n            exit "$RC"', process)
        self.assertIn("env.BRIDGE_PERSIST_DATABASE == 'true' && secrets.BRIDGE_WORKER_DATABASE_URL || ''",
                      step("Build quality-first Diana longitudinal candidates (dynamic-learning v3)"))
        terminal = step("Persist completed raw evidence and analysis to Neon")
        self.assertIn("steps.terminal.outputs.already_completed == 'true'", terminal)
        self.assertNotIn("steps.process.outcome == 'success'", terminal)
        self.assertIn("env.BRIDGE_PERSIST_DATABASE == 'true'", step("Record durable workflow final checkpoint"))


if __name__ == "__main__":
    unittest.main()
