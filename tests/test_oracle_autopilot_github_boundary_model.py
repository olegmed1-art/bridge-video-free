"""Independent finite-domain model for the public GitHub PR capability."""

from __future__ import annotations

import itertools

import pytest

from oracle_autopilot.contract import (
    AutopilotContractError,
    ClaimedTask,
    validate_task_contract,
)


ALLOWED_REPOSITORY = "olegmed1-art/bridge-video-free"
ALLOWED_HEAD = "a" * 40


def _model_accepts(goal: dict, *, step: str, cost: int) -> bool:
    return (
        set(goal)
        == {"repository", "pr_number", "expected_head_sha", "require_draft"}
        and goal.get("repository") == ALLOWED_REPOSITORY
        and type(goal.get("pr_number")) is int
        and 1 <= goal["pr_number"] <= 1_000_000
        and goal.get("expected_head_sha") == ALLOWED_HEAD
        and goal.get("require_draft") is True
        and step == "github.pr.snapshot"
        and cost == 0
    )


def _implementation_accepts(goal: dict, *, step: str, cost: int) -> bool:
    task = ClaimedTask(
        task_id="00000000-0000-0000-0000-000000000001",
        goal_type="GITHUB_PR_READ_ONLY_V1",
        goal_json=goal,
        current_step_key=step,
        step_cursor=0,
        lease_epoch=1,
        attempts=1,
        max_attempts=3,
        cost_cap_microusd=cost,
        cost_reserved_microusd=0,
    )
    try:
        validate_task_contract(task)
    except AutopilotContractError:
        return False
    return True


@pytest.mark.parametrize(
    ("repository", "pr_number", "head", "require_draft", "step", "cost"),
    itertools.product(
        [ALLOWED_REPOSITORY, "other/repository", "", None],
        [991, 1, 1_000_000, 0, 1_000_001, True, "991", None],
        [ALLOWED_HEAD, "A" * 40, "main", "a" * 39, None],
        [True, False, 1, None],
        ["github.pr.snapshot", "shell.exec", "", None],
        [0, 1, -1],
    ),
)
def test_runtime_contract_matches_independent_finite_model(
    repository, pr_number, head, require_draft, step, cost
):
    goal = {
        "repository": repository,
        "pr_number": pr_number,
        "expected_head_sha": head,
        "require_draft": require_draft,
    }
    assert _implementation_accepts(goal, step=step, cost=cost) is _model_accepts(
        goal, step=step, cost=cost
    )


def test_extra_or_missing_fields_are_rejected_by_both_models():
    valid = {
        "repository": ALLOWED_REPOSITORY,
        "pr_number": 991,
        "expected_head_sha": ALLOWED_HEAD,
        "require_draft": True,
    }
    variants = [
        {**valid, "command": "id"},
        {key: value for key, value in valid.items() if key != "repository"},
    ]
    for goal in variants:
        assert not _model_accepts(goal, step="github.pr.snapshot", cost=0)
        assert not _implementation_accepts(goal, step="github.pr.snapshot", cost=0)
