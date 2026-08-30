from __future__ import annotations

import hashlib
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
    assert 'AUTOPILOT_SOURCE_REVISION\")" == "$SOURCE_REVISION"' in installer
    assert 'RELEASE_DIR="$RELEASES_DIR/$SOURCE_REVISION"' in installer
    assert 'chown -R root:"$AUTOPILOT_GROUP" "$AUTOPILOT_DIR/.venv"' in installer
    assert 'chmod -R g+rX,o-rwx "$AUTOPILOT_DIR/.venv"' in installer
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


def test_activation_workflow_is_exact_shadow_only_and_never_stops_oracle():
    workflow = open(
        ".github/workflows/oracle-autopilot-shadow-activation.yml", encoding="utf-8"
    ).read()
    assert "EXPECTED_STAGED_REVISION: edc7e8530f0aa3efa84910cb09ee459ec25f1cf6" in workflow
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
