"""Independent finite-domain model for the brokered draft-repair capability."""

from __future__ import annotations

import hashlib
import json
import re

import pytest

from oracle_autopilot.contract import (
    AutopilotContractError,
    ClaimedTask,
    validate_task_contract,
)


REPOSITORY = "olegmed1-art/bridge-video-free"
GOAL_KEYS = {
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
CHANGE_KEYS = {"content_utf8", "expected_blob_sha", "operation", "path"}
PATH = re.compile(r"docs/evidence/autopilot/[A-Za-z0-9_.-]+\.md")


def _branch(task_key: str) -> str:
    digest = hashlib.sha256(task_key.encode()).hexdigest()[:16]
    return f"autopilot/repair/{digest}"


def _goal() -> dict:
    task_key = "phase3b-independent-live-canary"
    goal = {
        "task_key": task_key,
        "repository": REPOSITORY,
        "base_branch": "main",
        "expected_base_sha": "a" * 40,
        "branch_name": _branch(task_key),
        "title": "[Autopilot draft] Independent live canary",
        "changes": [
            {
                "path": "docs/evidence/autopilot/phase3b-model-canary.md",
                "operation": "CREATE",
                "content_utf8": "Independent canary.\n",
                "expected_blob_sha": None,
            }
        ],
        "require_draft": True,
        "allow_merge": False,
        "allow_force_push": False,
        "production_mutation": False,
    }
    canonical = {
        "allow_force_push": goal["allow_force_push"],
        "allow_merge": goal["allow_merge"],
        "base_branch": goal["base_branch"],
        "branch_name": goal["branch_name"],
        "changes": [
            {
                "content_sha256": hashlib.sha256(
                    change["content_utf8"].encode()
                ).hexdigest(),
                "expected_blob_sha": change["expected_blob_sha"],
                "operation": change["operation"],
                "path": change["path"],
            }
            for change in goal["changes"]
        ],
        "expected_base_sha": goal["expected_base_sha"],
        "production_mutation": goal["production_mutation"],
        "repository": goal["repository"],
        "require_draft": goal["require_draft"],
        "task_key": goal["task_key"],
        "title": goal["title"],
    }
    goal["action_fingerprint"] = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return goal


def _model_accepts(goal: dict, *, step: str, cost: int) -> bool:
    if set(goal) != GOAL_KEYS or step != "github.draft_repair" or cost != 0:
        return False
    task_key = goal.get("task_key")
    changes = goal.get("changes")
    if (
        not isinstance(task_key, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}", task_key) is None
        or goal.get("repository") != REPOSITORY
        or goal.get("base_branch") != "main"
        or re.fullmatch(r"[0-9a-f]{40}", str(goal.get("expected_base_sha"))) is None
        or goal.get("branch_name") != _branch(task_key)
        or not isinstance(goal.get("title"), str)
        or not str(goal["title"]).startswith("[Autopilot draft] ")
        or goal.get("require_draft") is not True
        or goal.get("allow_merge") is not False
        or goal.get("allow_force_push") is not False
        or goal.get("production_mutation") is not False
        or not isinstance(changes, list)
        or not 1 <= len(changes) <= 3
    ):
        return False
    total = 0
    seen: set[str] = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != CHANGE_KEYS:
            return False
        path = change.get("path")
        content = change.get("content_utf8")
        operation = change.get("operation")
        expected_blob = change.get("expected_blob_sha")
        if (
            not isinstance(path, str)
            or PATH.fullmatch(path) is None
            or path in seen
            or not isinstance(content, str)
            or not content
            or "\x00" in content
            or operation not in {"CREATE", "UPDATE"}
            or (operation == "CREATE" and expected_blob is not None)
            or (
                operation == "UPDATE"
                and (
                    not isinstance(expected_blob, str)
                    or re.fullmatch(r"[0-9a-f]{40}", expected_blob) is None
                )
            )
        ):
            return False
        size = len(content.encode())
        if size > 16_384:
            return False
        total += size
        seen.add(path)
    if total > 32_768:
        return False
    canonical = {
        "allow_force_push": goal["allow_force_push"],
        "allow_merge": goal["allow_merge"],
        "base_branch": goal["base_branch"],
        "branch_name": goal["branch_name"],
        "changes": [
            {
                "content_sha256": hashlib.sha256(
                    change["content_utf8"].encode()
                ).hexdigest(),
                "expected_blob_sha": change["expected_blob_sha"],
                "operation": change["operation"],
                "path": change["path"],
            }
            for change in changes
        ],
        "expected_base_sha": goal["expected_base_sha"],
        "production_mutation": goal["production_mutation"],
        "repository": goal["repository"],
        "require_draft": goal["require_draft"],
        "task_key": goal["task_key"],
        "title": goal["title"],
    }
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return goal.get("action_fingerprint") == expected


def _implementation_accepts(goal: dict, *, step: str, cost: int) -> bool:
    task = ClaimedTask(
        task_id="00000000-0000-0000-0000-000000000001",
        goal_type="GITHUB_DRAFT_REPAIR_V1",
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
    "mutator",
    [
        lambda goal: goal,
        lambda goal: {**goal, "repository": "other/repository"},
        lambda goal: {**goal, "base_branch": "production"},
        lambda goal: {**goal, "expected_base_sha": "main"},
        lambda goal: {**goal, "branch_name": "autopilot/repair/forged0000000000"},
        lambda goal: {**goal, "allow_merge": True},
        lambda goal: {**goal, "allow_force_push": True},
        lambda goal: {**goal, "production_mutation": True},
        lambda goal: {**goal, "action_fingerprint": "f" * 64},
        lambda goal: {**goal, "require_draft": False},
        lambda goal: {**goal, "command": "id"},
        lambda goal: {
            **goal,
            "changes": [{**goal["changes"][0], "path": ".github/workflows/pwn.yml"}],
        },
        lambda goal: {
            **goal,
            "changes": [{**goal["changes"][0], "expected_blob_sha": "b" * 40}],
        },
    ],
)
@pytest.mark.parametrize("step,cost", [("github.draft_repair", 0), ("shell.exec", 0), ("github.draft_repair", 1)])
def test_runtime_contract_matches_independent_model(mutator, step, cost):
    goal = mutator(_goal())
    assert _implementation_accepts(goal, step=step, cost=cost) is _model_accepts(
        goal, step=step, cost=cost
    )


def test_worker_source_keeps_github_credentials_inside_broker():
    source = open("oracle_autopilot/worker.py", encoding="utf-8").read()
    for forbidden in (
        "AUTOPILOT_GITHUB_APP_ID",
        "AUTOPILOT_GITHUB_INSTALLATION_ID",
        "AUTOPILOT_GITHUB_PRIVATE_KEY",
        "ghs_",
    ):
        assert forbidden not in source
    assert "TOKEN_BROKER_HOST_PATTERN" in source
    assert "TOKEN_BROKER_REDIRECT_REJECTED" in source
    assert "X-Vercel-Protection-Bypass" in source
