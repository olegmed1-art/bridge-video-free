"""Synthetic durable wait used only to prove the Python Workflows boundary.

This module has no database, GitHub, Oracle, Drive, OpenAI, or production side
effects.  A later pilot will persist canonical state in a temporary Neon branch.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from vercel import workflow

from app.workflow import wf


class ShadowSignal(BaseModel, workflow.BaseHook):
    """A bounded synthetic event that may resume one shadow wait."""

    task_id: str = Field(min_length=1, max_length=128)
    provider_event_id: str = Field(min_length=1, max_length=128)
    outcome: Literal["success", "failure"]


@wf.step
async def enter_shadow_wait(*, task_id: str) -> dict[str, str]:
    """Return deterministic evidence that the workflow reached its wait step."""

    return {
        "task_id": task_id,
        "state": "WAITING_EXTERNAL",
        "evidence_class": "SYNTHETIC_SHADOW_WAIT",
    }


@wf.step
async def evaluate_shadow_signal(
    *, task_id: str, signal: ShadowSignal
) -> dict[str, str]:
    """Apply the exact correlation and terminal contract for the spike."""

    if signal.task_id != task_id:
        raise ValueError("SHADOW_SIGNAL_TASK_MISMATCH")

    return {
        "task_id": task_id,
        "provider_event_id": signal.provider_event_id,
        "state": "DONE" if signal.outcome == "success" else "FAILED_CLOSED",
        "outcome": signal.outcome,
        "evidence_class": "SYNTHETIC_SHADOW_RESUME",
    }


@wf.workflow
async def shadow_wait_workflow(*, task_id: str, hook_token: str) -> dict[str, str]:
    """Suspend once and terminate from one correlated synthetic event."""

    await enter_shadow_wait(task_id=task_id)

    async for signal in ShadowSignal.wait(token=hook_token):
        return await evaluate_shadow_signal(task_id=task_id, signal=signal)

    raise RuntimeError("SHADOW_WAIT_ENDED_WITHOUT_EVENT")
