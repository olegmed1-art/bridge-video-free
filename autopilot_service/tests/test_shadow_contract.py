from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from vercel import workflow

from autopilot_app.main import (
    ShadowStartRequest,
    _require_shadow_authorization,
    _start_shadow_workflow,
    healthz,
)
from autopilot_app.workflow import wf
from autopilot_app.workflows import ShadowSignal, shadow_wait_workflow


STRONG_SECRET = "s" * 32


class ShadowContractTests(unittest.TestCase):
    def test_registry_exposes_observed_public_api(self) -> None:
        self.assertTrue(callable(getattr(wf, "workflow", None)))
        self.assertTrue(callable(getattr(wf, "step", None)))
        self.assertTrue(callable(getattr(workflow, "start", None)))
        self.assertFalse(callable(shadow_wait_workflow))
        self.assertTrue(bool(getattr(shadow_wait_workflow, "workflow_id", "")))
        self.assertIsInstance(getattr(workflow.Run("run-1"), "run_id"), str)

    def test_start_request_is_bounded(self) -> None:
        request = ShadowStartRequest(task_id="task:shadow-1")
        self.assertEqual(request.task_id, "task:shadow-1")

        with self.assertRaises(ValidationError):
            ShadowStartRequest(task_id="bad task id")

        with self.assertRaises(ValidationError):
            ShadowStartRequest(task_id="")

    def test_signal_schema_is_bounded(self) -> None:
        signal = ShadowSignal(
            task_id="task-1",
            provider_event_id="event-1",
            outcome="success",
        )
        self.assertEqual(signal.outcome, "success")

        with self.assertRaises(ValidationError):
            ShadowSignal(
                task_id="",
                provider_event_id="event-1",
                outcome="success",
            )

        with self.assertRaises(ValidationError):
            ShadowSignal(
                task_id="task-1",
                provider_event_id="event-1",
                outcome="unknown",
            )

    def test_control_fails_closed_when_secret_is_absent_or_weak(self) -> None:
        for env in ({}, {"AUTOPILOT_SHADOW_SECRET": "too-short"}):
            with self.subTest(env=env):
                with patch.dict(os.environ, env, clear=True):
                    with self.assertRaises(HTTPException) as ctx:
                        _require_shadow_authorization("Bearer value")
                self.assertEqual(ctx.exception.status_code, 503)
                self.assertEqual(ctx.exception.detail, "SHADOW_CONTROL_NOT_CONFIGURED")

    def test_control_auth_is_exact_and_constant_time_boundary(self) -> None:
        with patch.dict(os.environ, {"AUTOPILOT_SHADOW_SECRET": STRONG_SECRET}):
            with self.assertRaises(HTTPException) as ctx:
                _require_shadow_authorization("Bearer wrong")
            self.assertEqual(ctx.exception.status_code, 401)
            _require_shadow_authorization(f"Bearer {STRONG_SECRET}")

    def test_start_uses_exact_observed_api_and_returns_run_id(self) -> None:
        fake_run = SimpleNamespace(run_id="run-shadow-1")
        with patch("autopilot_app.main.workflow.start", return_value=fake_run) as start:
            run_id = _start_shadow_workflow(
                task_id="task-shadow-1",
                hook_token="x" * 43,
            )
        self.assertEqual(run_id, "run-shadow-1")
        start.assert_called_once_with(
            shadow_wait_workflow,
            task_id="task-shadow-1",
            hook_token="x" * 43,
        )

    def test_start_rejects_invalid_run_id(self) -> None:
        with patch(
            "autopilot_app.main.workflow.start",
            return_value=SimpleNamespace(run_id=""),
        ):
            with self.assertRaisesRegex(RuntimeError, "SHADOW_WORKFLOW_RUN_ID_INVALID"):
                _start_shadow_workflow(task_id="task-shadow-1", hook_token="x" * 43)

    def test_health_is_non_mutating_and_fail_closed_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            payload = asyncio.run(healthz())
        self.assertTrue(payload["shadow_only"])
        self.assertFalse(payload["production_mutations_enabled"])
        self.assertTrue(payload["workflow_start_supported"])
        self.assertFalse(payload["workflow_start_enabled"])
        self.assertFalse(payload["resume_configured"])

    def test_health_enables_only_shadow_control_when_strong_secret_exists(self) -> None:
        with patch.dict(os.environ, {"AUTOPILOT_SHADOW_SECRET": STRONG_SECRET}):
            payload = asyncio.run(healthz())
        self.assertFalse(payload["production_mutations_enabled"])
        self.assertTrue(payload["workflow_start_enabled"])
        self.assertTrue(payload["resume_configured"])

    def test_project_declares_pinned_workflow_sdk_and_registry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"vercel==0.10.0"', pyproject)
        self.assertIn('entrypoint = "autopilot_app.workflows:wf"', pyproject)
        self.assertIn('packages = ["autopilot_app"]', pyproject)


if __name__ == "__main__":
    unittest.main()
