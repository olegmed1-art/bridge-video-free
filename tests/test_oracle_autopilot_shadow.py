from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from oracle_autopilot.contract import (
    AutopilotContractError,
    ClaimedTask,
    claimed_task_from_row,
    validate_task_contract,
)
from oracle_autopilot.worker import (
    WorkerConfig,
    drain_ready,
    load_config,
    validate_neon_direct_dsn,
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


def test_fencing_and_cost_state_are_checked_before_execution():
    with pytest.raises(AutopilotContractError, match="LEASE_INVALID"):
        validate_task_contract(_task(lease_epoch=0))
    with pytest.raises(AutopilotContractError, match="COST_STATE_INVALID"):
        validate_task_contract(_task(cost_cap_microusd=10, cost_reserved_microusd=11))


def test_worker_source_has_no_arbitrary_execution_primitives():
    source = open("oracle_autopilot/worker.py", encoding="utf-8").read()
    for forbidden in ("subprocess", "os.system", "shell=True", "exec(", "eval("):
        assert forbidden not in source


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


def test_staging_installs_an_immutable_isolated_source_release():
    installer = open("ops/oracle_autopilot_shadow_install.sh", encoding="utf-8").read()
    assert 'AUTOPILOT_SOURCE_REVISION must be a pinned commit' in installer
    assert 'RELEASE_DIR="$RELEASES_DIR/$SOURCE_REVISION"' in installer
    assert 'chown -R root:root "$AUTOPILOT_DIR/.venv"' in installer
    assert 'staging refuses to replace an active service' in installer
    assert 'staging refuses to retain an enabled service' in installer
    assert 'activated=0 inactive=1 disabled=1' in installer


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
