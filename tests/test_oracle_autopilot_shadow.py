from __future__ import annotations

import hashlib
import json
import os
from unittest.mock import patch

import pytest

from oracle_autopilot.contract import (
    AutopilotContractError,
    ClaimedTask,
    build_draft_repair_broker_payload,
    claimed_task_from_row,
    validate_task_contract,
)
from oracle_autopilot.worker import (
    WorkerConfig,
    drain_ready,
    execute_bounded_draft_repair,
    fetch_github_ci_snapshot,
    fetch_github_pr_snapshot,
    load_config,
    load_token_broker_config,
    validate_neon_direct_dsn,
)
from autopilot_phase3b.policy import (
    FileChange,
    RepairRequest,
    expected_branch_name,
    repair_fingerprint,
)


DIRECT_DSN = (
    "postgresql://autopilot_runtime_login:secret@"
    "ep-shadow.eu-central-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def _task(**overrides):
    values = {
        "task_id": "00000000-0000-0000-0000-000000000001",
        "goal_type": "AUTOPILOT_SMOKE_V1",
        "goal_json": {},
        "current_step_key": "shadow.noop",
        "step_cursor": 0,
        "lease_epoch": 1,
        "attempts": 1,
        "max_attempts": 3,
        "cost_cap_microusd": 0,
        "cost_reserved_microusd": 0,
    }
    values.update(overrides)
    return ClaimedTask(**values)


def _draft_goal(**overrides):
    task_key = "phase3b-live-canary-20260831-01"
    values = {
        "task_key": task_key,
        "repository": "olegmed1-art/bridge-video-free",
        "base_branch": "main",
        "expected_base_sha": "a" * 40,
        "branch_name": expected_branch_name(task_key),
        "title": "[Autopilot draft] Phase 3B live canary",
        "changes": [
            {
                "path": "docs/evidence/autopilot/phase3b-canary.md",
                "operation": "CREATE",
                "content_utf8": "Bounded Phase 3B canary.\n",
                "expected_blob_sha": None,
            }
        ],
        "require_draft": True,
        "allow_merge": False,
        "allow_force_push": False,
        "production_mutation": False,
    }
    request = RepairRequest(
        task_key=values["task_key"],
        repository=values["repository"],
        base_branch=values["base_branch"],
        expected_base_sha=values["expected_base_sha"],
        branch_name=values["branch_name"],
        title=values["title"],
        changes=tuple(FileChange(**change) for change in values["changes"]),
    )
    values["action_fingerprint"] = repair_fingerprint(request)
    values.update(overrides)
    return values


def test_direct_neon_dsn_is_required_for_listen_notify(monkeypatch):
    monkeypatch.setenv("AUTOPILOT_EXPECTED_DB_USER", "autopilot_runtime_login")
    assert validate_neon_direct_dsn(DIRECT_DSN) == DIRECT_DSN

    with pytest.raises(RuntimeError, match="direct Neon endpoint"):
        validate_neon_direct_dsn(DIRECT_DSN.replace("ep-shadow.", "ep-shadow-pooler."))


@pytest.mark.parametrize(
    "bad_dsn",
    [
        "https://example.com",
        "postgresql://user:secret@example.com/neondb?sslmode=require&channel_binding=require",
        "postgresql://user:secret@ep-shadow.eu-central-1.aws.neon.tech/other?sslmode=require&channel_binding=require",
        "postgresql://user:secret@ep-shadow.eu-central-1.aws.neon.tech/neondb?sslmode=disable&channel_binding=require",
    ],
)
def test_invalid_database_boundaries_fail_closed(bad_dsn, monkeypatch):
    monkeypatch.delenv("AUTOPILOT_EXPECTED_DB_USER", raising=False)
    with pytest.raises(RuntimeError):
        validate_neon_direct_dsn(bad_dsn)


def test_runtime_refuses_non_shadow_mode():
    env = {
        "AUTOPILOT_RUNTIME_MODE": "PRODUCTION",
        "AUTOPILOT_DATABASE_URL": DIRECT_DSN,
    }
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(RuntimeError, match="must be SHADOW"):
            load_config()


def test_runtime_loads_bounded_latency_contract():
    env = {
        "AUTOPILOT_RUNTIME_MODE": "SHADOW",
        "AUTOPILOT_DATABASE_URL": DIRECT_DSN,
        "AUTOPILOT_EXPECTED_DB_USER": "autopilot_runtime_login",
        "AUTOPILOT_LEASE_SECONDS": "60",
        "AUTOPILOT_HEARTBEAT_SECONDS": "15",
        "AUTOPILOT_RECOVERY_POLL_SECONDS": "30",
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_config()
    assert config.lease_seconds == 60
    assert config.heartbeat_seconds == 15
    assert config.recovery_poll_seconds == 30


def test_only_allowlisted_task_kinds_are_claimed():
    row = {
        "task_id": "00000000-0000-0000-0000-000000000001",
        "goal_type": "ARBITRARY_SHELL",
        "goal_json": {},
        "current_step_key": "shell.exec",
        "step_cursor": 0,
        "lease_epoch": 1,
        "attempts": 1,
        "max_attempts": 3,
        "cost_cap_microusd": 0,
        "cost_reserved_microusd": 0,
    }
    with pytest.raises(AutopilotContractError, match="CAPABILITY_UNKNOWN"):
        claimed_task_from_row(row)


def test_wait_task_requires_exact_state_and_correlation():
    valid = _task(
        goal_type="EXTERNAL_WAIT_SHADOW_V1",
        goal_json={"correlation_id": "shadow:1"},
        current_step_key="shadow.wait",
    )
    validate_task_contract(valid)

    with pytest.raises(AutopilotContractError, match="WAIT_CORRELATION_INVALID"):
        validate_task_contract(
            _task(
                goal_type="EXTERNAL_WAIT_SHADOW_V1",
                goal_json={},
                current_step_key="shadow.wait",
            )
        )


def test_github_pr_task_is_exactly_bounded():
    valid = _task(
        goal_type="GITHUB_PR_READ_ONLY_V1",
        goal_json={
            "repository": "olegmed1-art/bridge-video-free",
            "pr_number": 991,
            "expected_head_sha": "a" * 40,
            "require_draft": True,
        },
        current_step_key="github.pr.snapshot",
    )
    validate_task_contract(valid)

    for bad_goal in (
        {**valid.goal_json, "repository": "other/repository"},
        {**valid.goal_json, "expected_head_sha": "main"},
        {**valid.goal_json, "require_draft": False},
        {**valid.goal_json, "extra": "field"},
    ):
        with pytest.raises(AutopilotContractError):
            validate_task_contract(
                _task(
                    goal_type="GITHUB_PR_READ_ONLY_V1",
                    goal_json=bad_goal,
                    current_step_key="github.pr.snapshot",
                )
            )


def test_github_ci_task_is_exactly_bounded():
    valid = _task(
        goal_type="GITHUB_CI_READ_ONLY_V1",
        goal_json={
            "repository": "olegmed1-art/bridge-video-free",
            "pr_number": 991,
            "expected_head_sha": "a" * 40,
            "require_draft": True,
        },
        current_step_key="github.ci.snapshot",
    )
    validate_task_contract(valid)

    with pytest.raises(AutopilotContractError, match="GITHUB_STATE_INVALID"):
        validate_task_contract(
            _task(
                goal_type="GITHUB_CI_READ_ONLY_V1",
                goal_json=valid.goal_json,
                current_step_key="github.pr.snapshot",
            )
        )


def test_github_draft_repair_task_is_exactly_bounded():
    goal = _draft_goal()
    task = _task(
        goal_type="GITHUB_DRAFT_REPAIR_V1",
        goal_json=goal,
        current_step_key="github.draft_repair",
    )
    validate_task_contract(task)
    payload = build_draft_repair_broker_payload(goal)
    assert payload["manifest_version"] == 1
    assert len(payload["action_fingerprint"]) == 64

    for bad_goal in (
        {**goal, "allow_merge": True},
        {**goal, "branch_name": "autopilot/repair/forged"},
        {**goal, "command": "id"},
    ):
        with pytest.raises(AutopilotContractError):
            validate_task_contract(
                _task(
                    goal_type="GITHUB_DRAFT_REPAIR_V1",
                    goal_json=bad_goal,
                    current_step_key="github.draft_repair",
                )
            )


def test_token_broker_config_pins_preview_origin(monkeypatch):
    env = {
        "AUTOPILOT_TOKEN_BROKER_URL": (
            "https://bridge-school-autopilot-cslfiz83g-"
            "olegmed1-4368s-projects.vercel.app/v1/github/draft-repair"
        ),
        "AUTOPILOT_TOKEN_BROKER_SECRET": "s" * 64,
        "AUTOPILOT_VERCEL_BYPASS_SECRET": "b" * 64,
    }
    with patch.dict(os.environ, env, clear=True):
        config = load_token_broker_config()
    assert config.host.endswith("-olegmed1-4368s-projects.vercel.app")

    with patch.dict(
        os.environ,
        {**env, "AUTOPILOT_TOKEN_BROKER_URL": "https://example.com/v1/github/draft-repair"},
        clear=True,
    ):
        with pytest.raises(AutopilotContractError, match="ORIGIN_INVALID"):
            load_token_broker_config()


def test_bounded_draft_repair_posts_only_to_pinned_broker(monkeypatch):
    goal = _draft_goal()
    request_payload = build_draft_repair_broker_payload(goal)
    response_payload = {
        "status": "created",
        "repository": goal["repository"],
        "task_key": goal["task_key"],
        "action_fingerprint": request_payload["action_fingerprint"],
        "manifest_version": 1,
        "base_sha": goal["expected_base_sha"],
        "branch_name": goal["branch_name"],
        "commit_sha": "b" * 40,
        "pull_request_number": 1001,
        "pull_request_url": "https://github.com/olegmed1-art/bridge-video-free/pull/1001",
        "draft": True,
        "replayed": False,
        "token_exposed": False,
        "merge_allowed": False,
        "production_mutation": False,
        "operation_count": 10,
    }
    broker_url = (
        "https://bridge-school-autopilot-cslfiz83g-"
        "olegmed1-4368s-projects.vercel.app/v1/github/draft-repair"
    )
    observed = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return broker_url

        def read(self, limit):
            observed["read_limit"] = limit
            return json.dumps(response_payload).encode()

    class Opener:
        @staticmethod
        def open(request, timeout):
            observed.update(
                {
                    "url": request.full_url,
                    "method": request.get_method(),
                    "authorization": request.get_header("Authorization"),
                    "bypass": request.get_header("X-vercel-protection-bypass"),
                    "timeout": timeout,
                    "request": json.loads(request.data),
                }
            )
            return Response()

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: Opener())
    with patch.dict(
        os.environ,
        {
            "AUTOPILOT_TOKEN_BROKER_URL": broker_url,
            "AUTOPILOT_TOKEN_BROKER_SECRET": "s" * 64,
            "AUTOPILOT_VERCEL_BYPASS_SECRET": "b" * 64,
        },
        clear=True,
    ):
        summary = execute_bounded_draft_repair(goal)

    assert summary["pull_request_number"] == 1001
    assert summary["token_exposed"] is False
    assert observed == {
        "url": broker_url,
        "method": "POST",
        "authorization": f"Bearer {'s' * 64}",
        "bypass": "b" * 64,
        "timeout": 30,
        "request": request_payload,
        "read_limit": 32_769,
    }


def test_bounded_draft_repair_rejects_forged_evidence(monkeypatch):
    goal = _draft_goal()
    request_payload = build_draft_repair_broker_payload(goal)
    response_payload = {
        "status": "created",
        "repository": goal["repository"],
        "task_key": goal["task_key"],
        "action_fingerprint": request_payload["action_fingerprint"],
        "manifest_version": 1,
        "base_sha": goal["expected_base_sha"],
        "branch_name": goal["branch_name"],
        "commit_sha": "b" * 40,
        "pull_request_number": 1001,
        "pull_request_url": "https://github.com/olegmed1-art/bridge-video-free/pull/1001",
        "draft": True,
        "replayed": False,
        "token_exposed": True,
        "merge_allowed": False,
        "production_mutation": False,
        "operation_count": 10,
    }
    broker_url = (
        "https://bridge-school-autopilot-cslfiz83g-"
        "olegmed1-4368s-projects.vercel.app/v1/github/draft-repair"
    )

    class Response:
        status = 200

        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def geturl(self): return broker_url
        def read(self, _limit): return json.dumps(response_payload).encode()

    class Opener:
        open = staticmethod(lambda *_args, **_kwargs: Response())

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: Opener())
    with patch.dict(
        os.environ,
        {
            "AUTOPILOT_TOKEN_BROKER_URL": broker_url,
            "AUTOPILOT_TOKEN_BROKER_SECRET": "s" * 64,
            "AUTOPILOT_VERCEL_BYPASS_SECRET": "b" * 64,
        },
        clear=True,
    ):
        with pytest.raises(AutopilotContractError, match="RESPONSE_INVALID"):
            execute_bounded_draft_repair(goal)


def test_github_pr_snapshot_uses_bounded_public_get(monkeypatch):
    expected_head = "b" * 40
    payload = {
        "number": 991,
        "html_url": "https://github.com/olegmed1-art/bridge-video-free/pull/991",
        "state": "open",
        "draft": True,
        "head": {"sha": expected_head},
        "mergeable": True,
        "updated_at": "2026-08-30T18:38:43Z",
    }
    observed = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/991"

        def read(self, limit):
            observed["read_limit"] = limit
            return json.dumps(payload).encode()

    def fake_open(request, timeout):
        observed["url"] = request.full_url
        observed["method"] = request.get_method()
        observed["authorization"] = request.get_header("Authorization")
        observed["timeout"] = timeout
        return Response()

    class Opener:
        open = staticmethod(fake_open)

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: Opener())
    summary = fetch_github_pr_snapshot(
        {
            "repository": "olegmed1-art/bridge-video-free",
            "pr_number": 991,
            "expected_head_sha": expected_head,
            "require_draft": True,
        }
    )
    assert summary["head_sha"] == expected_head
    assert summary["production_mutation"] is False
    assert summary["cost_actual_microusd"] == 0
    assert observed == {
        "url": "https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/991",
        "method": "GET",
        "authorization": None,
        "timeout": 15,
        "read_limit": 1_048_577,
    }


def test_github_pr_snapshot_fails_closed_on_head_change(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return "https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/991"

        def read(self, _limit):
            return json.dumps(
                {
                    "number": 991,
                    "html_url": "https://github.com/olegmed1-art/bridge-video-free/pull/991",
                    "state": "open",
                    "draft": True,
                    "head": {"sha": "c" * 40},
                    "mergeable": None,
                    "updated_at": "2026-08-30T18:38:43Z",
                }
            ).encode()

    class Opener:
        open = staticmethod(lambda *_args, **_kwargs: Response())

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: Opener())
    with pytest.raises(AutopilotContractError, match="GITHUB_PR_HEAD_CHANGED"):
        fetch_github_pr_snapshot(
            {
                "repository": "olegmed1-art/bridge-video-free",
                "pr_number": 991,
                "expected_head_sha": "b" * 40,
                "require_draft": True,
            }
        )


def test_github_pr_snapshot_rejects_redirect_before_following(monkeypatch):
    observed = {"redirect_attempts": 0}

    class RedirectResponse:
        def geturl(self):
            return "https://attacker.invalid/collect"

    class Opener:
        def open(self, request, timeout):
            handler = observed["handler"]
            observed["redirect_attempts"] += 1
            return handler.redirect_request(
                request,
                RedirectResponse(),
                302,
                "Found",
                {},
                "https://attacker.invalid/collect",
            )

    def fake_build_opener(handler):
        observed["handler"] = handler
        return Opener()

    monkeypatch.setattr("urllib.request.build_opener", fake_build_opener)
    with pytest.raises(AutopilotContractError, match="GITHUB_API_REDIRECT_REJECTED"):
        fetch_github_pr_snapshot(
            {
                "repository": "olegmed1-art/bridge-video-free",
                "pr_number": 991,
                "expected_head_sha": "b" * 40,
                "require_draft": True,
            }
        )
    assert observed["redirect_attempts"] == 1


def test_github_ci_snapshot_is_exact_bounded_and_read_only(monkeypatch):
    expected_head = "b" * 40
    pr_url = "https://api.github.com/repos/olegmed1-art/bridge-video-free/pulls/991"
    checks_url = (
        "https://api.github.com/repos/olegmed1-art/bridge-video-free/commits/"
        f"{expected_head}/check-runs?filter=latest&per_page=100"
    )
    annotations_url = (
        "https://api.github.com/repos/olegmed1-art/bridge-video-free/"
        "check-runs/22/annotations?per_page=2"
    )
    pr_payload = {
        "number": 991,
        "html_url": "https://github.com/olegmed1-art/bridge-video-free/pull/991",
        "state": "open",
        "draft": True,
        "head": {"sha": expected_head},
        "mergeable": True,
        "updated_at": "2026-08-30T20:00:00Z",
    }
    checks_payload = {
        "total_count": 2,
        "check_runs": [
            {
                "id": 11,
                "name": "contract",
                "head_sha": expected_head,
                "status": "completed",
                "conclusion": "success",
                "app": {"slug": "github-actions"},
                "output": {"title": "Passed", "summary": "All contracts passed."},
            },
            {
                "id": 22,
                "name": "postgresql-18",
                "head_sha": expected_head,
                "status": "completed",
                "conclusion": "failure",
                "app": {"slug": "github-actions"},
                "output": {
                    "title": "Migration failed",
                    "summary": "AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID",
                },
            },
        ],
    }
    annotations_payload = [
        {
            "annotation_level": "failure",
            "path": "database/migrations/0302_autopilot_github_ci_read_only.sql",
            "message": "PostgreSQL contract rejected the evidence shape.",
        }
    ]
    payloads = {
        pr_url: [pr_payload, pr_payload],
        checks_url: [checks_payload],
        annotations_url: [annotations_payload],
    }
    observed = []

    class Response:
        status = 200

        def __init__(self, url, payload):
            self.url = url
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return self.url

        def read(self, limit):
            observed[-1]["read_limit"] = limit
            return json.dumps(self.payload).encode()

    class Opener:
        @staticmethod
        def open(request, timeout):
            url = request.full_url
            observed.append(
                {
                    "url": url,
                    "method": request.get_method(),
                    "authorization": request.get_header("Authorization"),
                    "timeout": timeout,
                }
            )
            return Response(url, payloads[url].pop(0))

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: Opener())
    summary = fetch_github_ci_snapshot(
        {
            "repository": "olegmed1-art/bridge-video-free",
            "pr_number": 991,
            "expected_head_sha": expected_head,
            "require_draft": True,
        }
    )

    assert summary["overall_state"] == "FAIL"
    assert summary["check_total"] == 2
    assert summary["completed_count"] == 2
    assert summary["pending_count"] == 0
    assert summary["conclusion_counts"]["failure"] == 1
    assert summary["conclusion_counts"]["success"] == 1
    assert summary["failure_codes"] == ["CI_FAILURE"]
    assert summary["failed_checks"] == [
        {
            "app_slug": "github-actions",
            "check_run_id": 22,
            "conclusion": "failure",
            "name": "postgresql-18",
            "output_title": "Migration failed",
            "output_summary_excerpt": "AUTOPILOT_GITHUB_CI_EVIDENCE_INVALID",
            "output_truncated": False,
            "annotation_level": "failure",
            "annotation_path": "database/migrations/0302_autopilot_github_ci_read_only.sql",
            "annotation_message_excerpt": "PostgreSQL contract rejected the evidence shape.",
            "annotations_truncated": False,
        }
    ]
    assert summary["production_mutation"] is False
    assert summary["model_calls"] == 0
    assert summary["cost_actual_microusd"] == 0
    assert [item["url"] for item in observed] == [
        pr_url,
        checks_url,
        annotations_url,
        pr_url,
    ]
    assert all(
        item["method"] == "GET"
        and item["authorization"] is None
        and item["timeout"] == 15
        and item["read_limit"] == 1_048_577
        for item in observed
    )


def test_github_ci_snapshot_rejects_incomplete_check_set(monkeypatch):
    expected_head = "b" * 40
    pr_payload = {
        "number": 991,
        "html_url": "https://github.com/olegmed1-art/bridge-video-free/pull/991",
        "state": "open",
        "draft": True,
        "head": {"sha": expected_head},
        "mergeable": None,
        "updated_at": "2026-08-30T20:00:00Z",
    }
    responses = iter(
        [
            pr_payload,
            {"total_count": 101, "check_runs": []},
        ]
    )

    class Response:
        status = 200

        def __init__(self, request):
            self.request = request
            self.payload = next(responses)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def geturl(self):
            return self.request.full_url

        def read(self, _limit):
            return json.dumps(self.payload).encode()

    class Opener:
        open = staticmethod(lambda request, timeout: Response(request))

    monkeypatch.setattr("urllib.request.build_opener", lambda *_handlers: Opener())
    with pytest.raises(AutopilotContractError, match="CI_CHECK_SET_INCOMPLETE"):
        fetch_github_ci_snapshot(
            {
                "repository": "olegmed1-art/bridge-video-free",
                "pr_number": 991,
                "expected_head_sha": expected_head,
                "require_draft": True,
            }
        )


def test_fencing_and_cost_state_are_checked_before_execution():
    with pytest.raises(AutopilotContractError, match="LEASE_INVALID"):
        validate_task_contract(_task(lease_epoch=0))
    with pytest.raises(AutopilotContractError, match="COST_STATE_INVALID"):
        validate_task_contract(_task(cost_cap_microusd=10, cost_reserved_microusd=11))


def test_worker_source_has_no_arbitrary_execution_primitives():
    source = open("oracle_autopilot/worker.py", encoding="utf-8").read()
    for forbidden in ("subprocess", "os.system", "shell=True", "exec(", "eval("):
        assert forbidden not in source
    assert "GITHUB_TOKEN" not in source
    assert "ghs_" not in source
    assert "X-Vercel-Protection-Bypass" in source


def test_systemd_unit_is_shadow_only_and_resource_bounded():
    unit = open(
        "deploy/oracle-autopilot/school-autopilot-shadow.service", encoding="utf-8"
    ).read()
    assert "Environment=AUTOPILOT_RUNTIME_MODE=SHADOW" in unit
    assert "Restart=always" in unit
    assert "MemoryMax=768M" in unit
    assert "CPUQuota=100%" in unit
    assert "NoNewPrivileges=true" in unit
    assert "WorkingDirectory=/opt/bridge-school/school-autopilot/current" in unit
    assert "WorkingDirectory=/opt/bridge-school/bridge-video-free" not in unit
    assert "ReadWritePaths=/opt/bridge-school/school-autopilot/runtime" in unit
    assert (
        "EnvironmentFile=/opt/bridge-school/school-autopilot/autopilot-broker.env"
        in unit
    )


def test_staging_installs_an_immutable_isolated_source_release():
    installer = open("ops/oracle_autopilot_shadow_install.sh", encoding="utf-8").read()
    assert 'AUTOPILOT_SOURCE_REVISION must be a pinned commit' in installer
    assert 'AUTOPILOT_SOURCE_REVISION\")" == "$SOURCE_REVISION"' in installer
    assert 'RELEASE_DIR="$RELEASES_DIR/$SOURCE_REVISION"' in installer
    assert 'chown -R root:"$AUTOPILOT_GROUP" "$AUTOPILOT_DIR/.venv"' in installer
    assert 'chmod -R g+rX,o-rwx "$AUTOPILOT_DIR/.venv"' in installer
    assert 'staging refuses to replace an active service' in installer
    assert 'staging refuses to retain an enabled service' in installer
    assert 'activated=0 inactive=1 disabled=1' in installer
    assert '"$REPO_DIR"/autopilot_phase3b/*.py' in installer


def test_staging_update_stops_only_autopilot_and_rolls_back_on_failure():
    workflow = open(
        ".github/workflows/oracle-autopilot-staging.yml", encoding="utf-8"
    ).read()
    assert 'request["replace_active_service"] is True' in workflow
    assert 'AUTOPILOT_REPLACE_ACTIVE' in workflow
    assert 'systemctl disable --now "$service"' in workflow
    assert 'systemctl enable --now "$service"' in workflow
    assert "AUTOPILOT_UPDATE_ROLLBACK_SERVICE_RESTORED" in workflow
    assert "AUTOPILOT_UPDATE_STOPPED_SERVICE_ONLY=YES" in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow
    assert "oci compute instance" not in workflow
    assert "--action STOP" not in workflow


def test_ready_queue_is_drained_without_a_poll_gap(monkeypatch):
    outcomes = iter((True, True, True, False))
    calls = []

    def fake_process(config):
        calls.append(config.worker_id)
        return next(outcomes)

    monkeypatch.setattr("oracle_autopilot.worker.process_one", fake_process)
    config = WorkerConfig(dsn=DIRECT_DSN, worker_id="test-worker")
    assert drain_ready(config) == 3
    assert calls == ["test-worker"] * 4


def test_activation_workflow_is_exact_shadow_only_and_never_stops_oracle():
    workflow = open(
        ".github/workflows/oracle-autopilot-shadow-activation.yml", encoding="utf-8"
    ).read()
    assert "EXPECTED_STAGED_REVISION: e2445f94c70c56ac9c1db9d3f3cdf930299196fa" in workflow
    unit_sha256 = hashlib.sha256(
        open("deploy/oracle-autopilot/school-autopilot-shadow.service", "rb").read()
    ).hexdigest()
    assert f"EXPECTED_UNIT_SHA256: {unit_sha256}" in workflow
    assert "request['activation_scope'] == 'SHADOW_ONLY'" in workflow
    assert "request['no_instance_stop'] is True" in workflow
    assert "request['neon_min_cu'] == 0.25" in workflow
    assert "request['neon_max_cu'] == 8" in workflow
    assert "request['runtime_connection_limit'] == 4" in workflow
    assert 'systemctl enable --now "$service"' in workflow
    assert "AUTOPILOT_PRODUCTION_MUTATIONS=NO" in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow
    assert 'if [[ "$activated_here" == 1 ]]; then' in workflow
    assert 'active_since="$(systemctl show -p ActiveEnterTimestamp --value' in workflow
    assert 'systemctl disable --now "$service"' in workflow
    assert "AUTOPILOT_SHADOW_ACTIVATION_ROLLED_BACK" in workflow
    assert 'sha256sum "$unit"' in workflow
    assert "AUTOPILOT_DIAG_NRESTARTS" in workflow
    assert "AUTOPILOT_DIAG_WORKER_STARTED_COUNT" in workflow
    assert "AUTOPILOT_DIAG_UNSAFE_JOURNAL_COUNT" in workflow
    assert 'echo "$journal"' not in workflow
    assert 'cmp -s "$unit" "$release/deploy/' not in workflow
    for forbidden in (
        "--action " + "STOP",
        "systemctl " + "stop",
        "oci compute instance " + "action",
    ):
        assert forbidden not in workflow


def test_shadow_diagnostics_are_read_only_and_secret_free():
    workflow = open(
        ".github/workflows/oracle-autopilot-shadow-diagnostics.yml", encoding="utf-8"
    ).read()
    assert "AUTOPILOT_DIAGNOSTIC_READ_ONLY=YES" in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow
    assert "AUTOPILOT_DIAG_MODULE_IMPORT_COUNT" in workflow
    assert "AUTOPILOT_DIAG_CONFIG_COUNT" in workflow
    assert "AUTOPILOT_DIAG_DATABASE_COUNT" in workflow
    assert "AUTOPILOT_DIAG_CHDIR_PERMISSION_COUNT" in workflow
    assert "AUTOPILOT_DIAG_EXEC_PERMISSION_COUNT" in workflow
    assert "AUTOPILOT_DIAG_ENV_PERMISSION_COUNT" in workflow
    for forbidden in (
        "systemctl " + "start",
        "systemctl " + "stop",
        "systemctl " + "restart",
        "systemctl " + "enable",
        "systemctl " + "disable",
        "echo \"$journal\"",
        'cat "$root/autopilot-shadow.env"',
    ):
        assert forbidden not in workflow


def test_oracle_power_workflow_has_no_automatic_trigger():
    workflow = open(
        ".github/workflows/oracle-instance-power.yml", encoding="utf-8"
    ).read()
    assert "\n  schedule:" not in workflow
    assert "\n  push:" not in workflow
