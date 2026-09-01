from __future__ import annotations

import json
from pathlib import Path

import pytest

from oracle_autopilot.online_observer import (
    ObserverConfig,
    _parse_tick,
    load_config,
    open_local_circuit,
    persist_finding,
    write_heartbeat,
)


DIRECT_DSN = (
    "postgresql://autopilot_runtime_login:secret@ep-test.eu-central-1.aws.neon.tech/"
    "neondb?sslmode=require&channel_binding=require"
)


def _config(state_dir: Path) -> ObserverConfig:
    return ObserverConfig(
        dsn=DIRECT_DSN,
        observer_id="oracle-online-observer-test",
        state_dir=state_dir,
    )


def test_config_is_shadow_only_and_rate_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOPILOT_RUNTIME_MODE", "SHADOW")
    monkeypatch.setenv("AUTOPILOT_DATABASE_URL", DIRECT_DSN)
    monkeypatch.setenv("AUTOPILOT_ONLINE_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("AUTOPILOT_ONLINE_MIN_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("AUTOPILOT_ONLINE_MAX_TASKS_PER_HOUR", "720")
    config = load_config()
    assert config.min_interval_seconds == 5
    assert config.max_tasks_per_hour == 720
    assert config.state_dir == tmp_path

    monkeypatch.setenv("AUTOPILOT_ONLINE_MIN_INTERVAL_SECONDS", "1")
    with pytest.raises(RuntimeError, match="MIN_INTERVAL"):
        load_config()

    monkeypatch.setenv("AUTOPILOT_RUNTIME_MODE", "PRODUCTION")
    with pytest.raises(RuntimeError, match="must be SHADOW"):
        load_config()


def test_tick_response_is_strict_and_fail_closed():
    row = {
        "action": "CREATED",
        "task_key": "phase3b-oracle-online-20260901T090000000Z-00000001",
        "task_status": "READY",
        "circuit_open": False,
        "finding_code": None,
        "created_count": 1,
        "pass_count": 0,
        "finding_count": 0,
    }
    result = _parse_tick(row)
    assert result.action == "CREATED"
    assert result.created_count == 1

    with pytest.raises(RuntimeError, match="TICK_RESPONSE_INVALID"):
        _parse_tick({**row, "action": "ARBITRARY"})
    with pytest.raises(RuntimeError, match="CIRCUIT_RESPONSE_INVALID"):
        _parse_tick({**row, "circuit_open": True})
    with pytest.raises(RuntimeError, match="COUNTER_INVALID"):
        _parse_tick({**row, "pass_count": 2})


def test_findings_and_heartbeat_are_secret_free_and_idempotent(tmp_path):
    config = _config(tmp_path)
    first = persist_finding(
        config,
        code="ONLINE_TEST_FINDING",
        task_key="phase3b-oracle-online-test-00000001",
        required_fix="Inspect the retained test evidence.",
    )
    second = persist_finding(
        config,
        code="ONLINE_TEST_FINDING",
        task_key="phase3b-oracle-online-test-00000001",
        required_fix="Inspect the retained test evidence.",
    )
    assert first == second
    assert len(list((tmp_path / "findings").glob("*.json"))) == 1

    open_local_circuit(
        config,
        code="ONLINE_TEST_FINDING",
        task_key="phase3b-oracle-online-test-00000001",
        required_fix="Inspect the retained test evidence.",
    )
    circuit = json.loads((tmp_path / "circuit-open.json").read_text())
    assert circuit["circuit_open"] is True
    assert "secret" not in json.dumps(circuit).lower()

    write_heartbeat(
        config,
        {
            "observer_mode": "SHADOW_ONLY",
            "circuit_open": True,
            "finding_count": 1,
        },
    )
    heartbeat = json.loads((tmp_path / "heartbeat.json").read_text())
    assert heartbeat["runtime_mode"] == "SHADOW_ONLY"
    assert heartbeat["observer_id"] == config.observer_id


def test_source_has_only_guarded_rpc_and_no_execution_or_model_primitives():
    source = Path("oracle_autopilot/online_observer.py").read_text()
    assert "autopilot.online_pilot_tick" in source
    assert "autopilot.online_pilot_status" in source
    assert "autopilot.task" not in source
    for forbidden in (
        "subprocess",
        "os.system",
        "shell=True",
        "exec(",
        "eval(",
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "autopilot-broker.env",
    ):
        assert forbidden not in source


def test_systemd_unit_is_isolated_shadow_only_and_resource_bounded():
    unit = Path(
        "deploy/oracle-autopilot/school-autopilot-online-observer.service"
    ).read_text()
    assert "Environment=AUTOPILOT_RUNTIME_MODE=SHADOW" in unit
    assert "AUTOPILOT_ONLINE_MIN_INTERVAL_SECONDS=5" in unit
    assert "AUTOPILOT_ONLINE_MAX_TASKS_PER_HOUR=720" in unit
    assert "StateDirectory=school-autopilot-online-observer" in unit
    assert "MemoryMax=256M" in unit
    assert "CPUQuota=20%" in unit
    assert "NoNewPrivileges=true" in unit
    assert "autopilot-broker.env" not in unit
    assert "oracle_autopilot.online_observer" in unit


def test_installer_and_workflow_never_restart_consumer_or_stop_oracle():
    installer = Path("ops/oracle_autopilot_online_observer_install.sh").read_text()
    workflow = Path(
        ".github/workflows/oracle-autopilot-online-observer.yml"
    ).read_text()
    assert "school-autopilot-shadow.service" in installer
    assert '"$REPO_DIR"/autopilot_phase3b/*.py' in installer
    assert "AUTOPILOT_CONSUMER_RESTARTED=NO" in installer
    assert "systemctl restart" not in installer
    assert "systemctl stop" not in installer
    assert "request['activation_scope'] == 'SHADOW_ONLY'" in workflow
    assert "request['no_instance_stop'] is True" in workflow
    assert "request['no_consumer_restart'] is True" in workflow
    assert "ORACLE_INSTANCE_STOP_REQUESTED=NO" in workflow
    assert "AUTOPILOT_PRODUCTION_MUTATIONS=NO" in workflow
    assert "oci compute instance action" not in workflow
    assert "--action STOP" not in workflow
