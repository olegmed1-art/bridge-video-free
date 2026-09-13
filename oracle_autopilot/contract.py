"""Bounded contracts for Oracle Autopilot Lite shadow execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from autopilot_phase3b.policy import (
    DraftRepairPolicyError,
    FileChange,
    RepairRequest,
    repair_fingerprint,
    validate_repair_request,
)


TaskKind = Literal[
    "AUTOPILOT_SMOKE_V1",
    "EXTERNAL_WAIT_SHADOW_V1",
    "OWNER_BOUNDARY_V1",
    "GITHUB_PR_READ_ONLY_V1",
    "GITHUB_CI_READ_ONLY_V1",
    "GITHUB_DRAFT_REPAIR_V1",
    "IBF_READ_ONLY_ANALYSIS",
]

ALLOWED_TASK_KINDS = frozenset(
    {
        "AUTOPILOT_SMOKE_V1",
        "EXTERNAL_WAIT_SHADOW_V1",
        "OWNER_BOUNDARY_V1",
        "GITHUB_PR_READ_ONLY_V1",
        "GITHUB_CI_READ_ONLY_V1",
        "GITHUB_DRAFT_REPAIR_V1",
        "IBF_READ_ONLY_ANALYSIS",
    }
)

DRAFT_REPAIR_GOAL_KEYS = frozenset(
    {
        "allow_force_push",
        "allow_merge",
        "action_fingerprint",
        "base_branch",
        "branch_name",
        "changes",
        "expected_base_sha",
        "production_mutation",
        "repository",
        "require_draft",
        "task_key",
        "title",
    }
)
DRAFT_REPAIR_CHANGE_KEYS = frozenset(
    {"content_utf8", "expected_blob_sha", "operation", "path"}
)
IBF_GOAL_KEYS = frozenset(
    {"approval_ref", "ibf_player_id", "source_authority"}
)
IBF_SOURCE_AUTHORITY = "ISRAEL_BRIDGE_FEDERATION_OFFICIAL_RESULTS"


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


def build_draft_repair_broker_payload(goal_json: dict[str, Any]) -> dict[str, Any]:
    """Revalidate a locked task goal and add broker-computed metadata."""

    if set(goal_json) != DRAFT_REPAIR_GOAL_KEYS:
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_FIELDS_INVALID")
    scalar_strings = (
        "base_branch",
        "branch_name",
        "expected_base_sha",
        "repository",
        "task_key",
        "title",
    )
    if any(not isinstance(goal_json.get(key), str) for key in scalar_strings):
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_FIELDS_INVALID")
    if not isinstance(goal_json.get("action_fingerprint"), str) or re.fullmatch(
        r"[0-9a-f]{64}", goal_json["action_fingerprint"]
    ) is None:
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_FINGERPRINT_INVALID")
    if (
        goal_json.get("require_draft") is not True
        or goal_json.get("allow_merge") is not False
        or goal_json.get("allow_force_push") is not False
        or goal_json.get("production_mutation") is not False
    ):
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_FLAGS_INVALID")
    raw_changes = goal_json.get("changes")
    if not isinstance(raw_changes, list) or not 1 <= len(raw_changes) <= 3:
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_CHANGES_INVALID")

    changes: list[FileChange] = []
    for raw_change in raw_changes:
        if not isinstance(raw_change, dict) or set(raw_change) != DRAFT_REPAIR_CHANGE_KEYS:
            raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_CHANGES_INVALID")
        if (
            not isinstance(raw_change.get("path"), str)
            or raw_change.get("operation") not in {"CREATE", "UPDATE"}
            or not isinstance(raw_change.get("content_utf8"), str)
            or (
                raw_change.get("expected_blob_sha") is not None
                and not isinstance(raw_change.get("expected_blob_sha"), str)
            )
        ):
            raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_CHANGES_INVALID")
        changes.append(
            FileChange(
                path=raw_change["path"],
                operation=raw_change["operation"],
                content_utf8=raw_change["content_utf8"],
                expected_blob_sha=raw_change["expected_blob_sha"],
            )
        )

    request = RepairRequest(
        task_key=goal_json["task_key"],
        repository=goal_json["repository"],
        base_branch=goal_json["base_branch"],
        expected_base_sha=goal_json["expected_base_sha"],
        branch_name=goal_json["branch_name"],
        title=goal_json["title"],
        changes=tuple(changes),
        require_draft=goal_json["require_draft"],
        allow_merge=goal_json["allow_merge"],
        allow_force_push=goal_json["allow_force_push"],
        production_mutation=goal_json["production_mutation"],
    )
    try:
        validate_repair_request(request)
        fingerprint = repair_fingerprint(request)
    except (DraftRepairPolicyError, TypeError, ValueError) as exc:
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_POLICY_INVALID") from exc

    if goal_json["action_fingerprint"] != fingerprint:
        raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_FINGERPRINT_INVALID")
    payload = dict(goal_json)
    payload["manifest_version"] = 1
    return payload


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

    if task.goal_type == "GITHUB_DRAFT_REPAIR_V1":
        build_draft_repair_broker_payload(task.goal_json)
        if task.current_step_key != "github.draft_repair" or task.step_cursor != 0:
            raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_STATE_INVALID")
        if task.cost_cap_microusd != 0 or task.cost_reserved_microusd != 0:
            raise AutopilotContractError("AUTOPILOT_DRAFT_REPAIR_COST_INVALID")
        return

    if task.goal_type == "IBF_READ_ONLY_ANALYSIS":
        if set(task.goal_json) != IBF_GOAL_KEYS:
            raise AutopilotContractError("AUTOPILOT_IBF_FIELDS_INVALID")
        player_id = task.goal_json.get("ibf_player_id")
        if not isinstance(player_id, str) or re.fullmatch(r"[1-9][0-9]{0,9}", player_id) is None:
            raise AutopilotContractError("AUTOPILOT_IBF_PLAYER_ID_INVALID")
        approval_ref = task.goal_json.get("approval_ref")
        if not isinstance(approval_ref, str) or re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._:/#-]{0,255}", approval_ref
        ) is None:
            raise AutopilotContractError("AUTOPILOT_APPROVAL_REF_INVALID")
        if task.goal_json.get("source_authority") != IBF_SOURCE_AUTHORITY:
            raise AutopilotContractError("AUTOPILOT_IBF_SOURCE_AUTHORITY_INVALID")
        if task.current_step_key != "ibf.read_only_analysis" or task.step_cursor != 0:
            raise AutopilotContractError("AUTOPILOT_IBF_STATE_INVALID")
        if task.cost_cap_microusd != 0 or task.cost_reserved_microusd != 0:
            raise AutopilotContractError("AUTOPILOT_IBF_COST_INVALID")
        return

    correlation_id = task.goal_json.get("correlation_id")
    if not isinstance(correlation_id, str) or not correlation_id:
        raise AutopilotContractError("AUTOPILOT_WAIT_CORRELATION_INVALID")
    if task.current_step_key != "shadow.wait" or task.step_cursor not in {0, 1}:
        raise AutopilotContractError("AUTOPILOT_WAIT_STATE_INVALID")
