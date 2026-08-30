"""Bounded contracts for Oracle Autopilot Lite shadow execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


TaskKind = Literal[
    "AUTOPILOT_SMOKE_V1",
    "EXTERNAL_WAIT_SHADOW_V1",
    "OWNER_BOUNDARY_V1",
    "GITHUB_PR_READ_ONLY_V1",
    "GITHUB_CI_READ_ONLY_V1",
]

ALLOWED_TASK_KINDS = frozenset(
    {
        "AUTOPILOT_SMOKE_V1",
        "EXTERNAL_WAIT_SHADOW_V1",
        "OWNER_BOUNDARY_V1",
        "GITHUB_PR_READ_ONLY_V1",
        "GITHUB_CI_READ_ONLY_V1",
    }
)


class AutopilotContractError(RuntimeError):
    """A permanent fail-closed contract violation."""


class AutopilotRetryableError(RuntimeError):
    """A bounded transient failure eligible for the existing retry contract."""


@dataclass(frozen=True)
class ClaimedTask:
    task_id: str
    goal_type: TaskKind
    goal_json: dict[str, Any]
    current_step_key: str
    step_cursor: int
    lease_epoch: int
    attempts: int
    max_attempts: int
    cost_cap_microusd: int
    cost_reserved_microusd: int


def claimed_task_from_row(row: dict[str, Any]) -> ClaimedTask:
    kind = str(row.get("goal_type") or "")
    if kind not in ALLOWED_TASK_KINDS:
        raise AutopilotContractError("AUTOPILOT_CAPABILITY_UNKNOWN")
    payload = row.get("goal_json")
    if not isinstance(payload, dict):
        raise AutopilotContractError("AUTOPILOT_GOAL_INVALID")
    return ClaimedTask(
        task_id=str(row["task_id"]),
        goal_type=kind,  # type: ignore[arg-type]
        goal_json=payload,
        current_step_key=str(row["current_step_key"]),
        step_cursor=int(row["step_cursor"]),
        lease_epoch=int(row["lease_epoch"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        cost_cap_microusd=int(row["cost_cap_microusd"]),
        cost_reserved_microusd=int(row["cost_reserved_microusd"]),
    )


def validate_task_contract(task: ClaimedTask) -> None:
    if task.lease_epoch < 1 or task.attempts < 1:
        raise AutopilotContractError("AUTOPILOT_LEASE_INVALID")
    if task.attempts > task.max_attempts:
        raise AutopilotContractError("AUTOPILOT_ATTEMPT_BUDGET_EXCEEDED")
    if task.cost_reserved_microusd > task.cost_cap_microusd:
        raise AutopilotContractError("AUTOPILOT_COST_STATE_INVALID")

    if task.goal_type == "AUTOPILOT_SMOKE_V1":
        if task.current_step_key != "shadow.noop" or task.step_cursor != 0:
            raise AutopilotContractError("AUTOPILOT_SMOKE_STATE_INVALID")
        return

    if task.goal_type == "OWNER_BOUNDARY_V1":
        if task.current_step_key != "policy.owner_boundary" or task.step_cursor != 0:
            raise AutopilotContractError("AUTOPILOT_OWNER_STATE_INVALID")
        return

    if task.goal_type in {"GITHUB_PR_READ_ONLY_V1", "GITHUB_CI_READ_ONLY_V1"}:
        expected_keys = {
            "repository",
            "pr_number",
            "expected_head_sha",
            "require_draft",
        }
        if set(task.goal_json) != expected_keys:
            raise AutopilotContractError("AUTOPILOT_GITHUB_FIELDS_INVALID")
        if task.goal_json["repository"] != "olegmed1-art/bridge-video-free":
            raise AutopilotContractError("AUTOPILOT_GITHUB_REPOSITORY_INVALID")
        pr_number = task.goal_json["pr_number"]
        if isinstance(pr_number, bool) or not isinstance(pr_number, int):
            raise AutopilotContractError("AUTOPILOT_GITHUB_PR_INVALID")
        if not 1 <= pr_number <= 1_000_000:
            raise AutopilotContractError("AUTOPILOT_GITHUB_PR_INVALID")
        expected_head = task.goal_json["expected_head_sha"]
        if not isinstance(expected_head, str) or not re.fullmatch(
            r"[0-9a-f]{40}", expected_head
        ):
            raise AutopilotContractError("AUTOPILOT_GITHUB_HEAD_INVALID")
        if task.goal_json["require_draft"] is not True:
            raise AutopilotContractError("AUTOPILOT_GITHUB_DRAFT_GATE_INVALID")
        expected_step = (
            "github.pr.snapshot"
            if task.goal_type == "GITHUB_PR_READ_ONLY_V1"
            else "github.ci.snapshot"
        )
        if task.current_step_key != expected_step or task.step_cursor != 0:
            raise AutopilotContractError("AUTOPILOT_GITHUB_STATE_INVALID")
        if task.cost_cap_microusd != 0 or task.cost_reserved_microusd != 0:
            raise AutopilotContractError("AUTOPILOT_GITHUB_COST_INVALID")
        return

    correlation_id = task.goal_json.get("correlation_id")
    if not isinstance(correlation_id, str) or not correlation_id:
        raise AutopilotContractError("AUTOPILOT_WAIT_CORRELATION_INVALID")
    if task.current_step_key != "shadow.wait" or task.step_cursor not in {0, 1}:
        raise AutopilotContractError("AUTOPILOT_WAIT_STATE_INVALID")
