from __future__ import annotations

import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.main import _require_shadow_authorization, healthz
from app.workflow import wf
from app.workflows import ShadowSignal, shadow_wait_workflow


class ShadowContractTests(unittest.TestCase):
    def test_registry_exposes_documented_decorators(self) -> None:
        self.assertTrue(callable(getattr(wf, "workflow", None)))
        self.assertTrue(callable(getattr(wf, "step", None)))
        self.assertTrue(callable(shadow_wait_workflow))

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

    def test_resume_fails_closed_when_secret_is_absent(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                _require_shadow_authorization("Bearer value")
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertEqual(ctx.exception.detail, "SHADOW_RESUME_NOT_CONFIGURED")

    def test_resume_auth_is_exact_and_constant_time_boundary(self) -> None:
        with patch.dict(os.environ, {"AUTOPILOT_SHADOW_SECRET": "expected"}):
            with self.assertRaises(HTTPException) as ctx:
                _require_shadow_authorization("Bearer wrong")
            self.assertEqual(ctx.exception.status_code, 401)
            _require_shadow_authorization("Bearer expected")

    def test_health_is_non_mutating_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            payload = asyncio.run(healthz())
        self.assertTrue(payload["shadow_only"])
        self.assertFalse(payload["production_mutations_enabled"])
        self.assertFalse(payload["workflow_start_enabled"])
        self.assertFalse(payload["resume_configured"])

    def test_project_declares_pinned_workflow_sdk_and_registry(self) -> None:
        root = Path(__file__).resolve().parents[1]
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"vercel==0.10.0"', pyproject)
        self.assertIn('entrypoint = "app.workflows:wf"', pyproject)


if __name__ == "__main__":
    unittest.main()
