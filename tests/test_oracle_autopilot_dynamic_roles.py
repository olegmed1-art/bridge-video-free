from __future__ import annotations

import pytest

from oracle_autopilot.contract import (
    AutopilotContractError,
    ClaimedTask,
    validate_task_contract,
)


def _task(goal_type: str, goal_json: dict) -> ClaimedTask:
    return ClaimedTask(
        task_id="550e8400-e29b-41d4-a716-446655440000",
        goal_type=goal_type,
        goal_json=goal_json,
        current_step_key="github.chatgpt.role.dispatch",
        step_cursor=0,
        lease_epoch=1,
        attempts=1,
        max_attempts=3,
        cost_cap_microusd=0,
        cost_reserved_microusd=0,
    )


def _dispatch_goal(role: str) -> dict:
    return {
        "repository": "olegmed1-art/bridge-video-free",
        "mailbox_pr": 1150,
        "role": role,
        "target_pr": 1200,
        "expected_head_sha": "a" * 40,
        "dispatch_epoch": 1,
        "successor_task_key": None,
        "successor_role": None,
        "successor_target_pr": None,
        "successor_expected_head_sha": None,
    }


@pytest.mark.parametrize("role", ["QA", "SECURITY", "DATA", "GENERAL", "VIDEO_QUEUE"])
def test_registry_shaped_roles_are_accepted(role: str) -> None:
    validate_task_contract(_task("CHATGPT_ROLE_DISPATCH_V1", _dispatch_goal(role)))


def test_registry_shaped_successor_role_is_accepted() -> None:
    goal = _dispatch_goal("PLANNING")
    goal.update(
        successor_task_key="next-task",
        successor_role="REPORTING",
        successor_target_pr=1150,
        successor_expected_head_sha="b" * 40,
    )
    validate_task_contract(_task("CHATGPT_ROLE_DISPATCH_V1", goal))


@pytest.mark.parametrize("role", ["qa", "BAD-ROLE", "_QA", "", "A" * 65])
def test_malformed_roles_remain_fail_closed(role: str) -> None:
    with pytest.raises(AutopilotContractError, match="ROLE_INVALID"):
        validate_task_contract(_task("CHATGPT_ROLE_DISPATCH_V1", _dispatch_goal(role)))
