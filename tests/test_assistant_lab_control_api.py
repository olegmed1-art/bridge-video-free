import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from assistant_lab.control_api import RunRequest, healthz, run


def _configure(monkeypatch, tmp_path: Path):
    token = "t" * 48
    registry = tmp_path / "tool_registry.json"
    registry.write_text(
        json.dumps({
            "schema": "assistant-lab-control-tools/v0.1",
            "tools": {"health.noop": {"argv": ["/bin/true"]}},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ASSISTANT_LAB_OBSERVER_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("ASSISTANT_LAB_CONTROL_REGISTRY", str(registry))
    monkeypatch.setenv("ASSISTANT_LAB_CONTROL_TOKEN", token)
    return token


def test_health_is_authenticated_and_fail_closed(monkeypatch, tmp_path):
    token = _configure(monkeypatch, tmp_path)
    result = healthz(f"Bearer {token}")
    assert result["status"] == "ready"
    assert result["tool_ids"] == ["health.noop"]
    assert result["arbitrary_shell"] is False
    assert result["video_analyzer_result_access"] is False
    assert result["other_oracle_result_access"] is False
    with pytest.raises(HTTPException) as exc:
        healthz("Bearer wrong")
    assert exc.value.status_code == 404


def test_run_uses_registry_argv_only(monkeypatch, tmp_path):
    token = _configure(monkeypatch, tmp_path)
    result = run(
        RunRequest(tool_id="health.noop", experiment_id="CTRL-TEST-1", timeout_seconds=30, label="control-test"),
        f"Bearer {token}",
    )
    assert result["status"] == "queued"
    assert result["experiment_id"] == "CTRL-TEST-1"
    job = json.loads((tmp_path / "jobs" / "pending" / "CTRL-TEST-1.json").read_text(encoding="utf-8"))
    assert job["command"] == ["/bin/true"]
    assert job["env"] == {}


def test_unknown_tool_cannot_become_arbitrary_command(monkeypatch, tmp_path):
    token = _configure(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc:
        run(RunRequest(tool_id="bash", experiment_id="CTRL-TEST-2"), f"Bearer {token}")
    assert exc.value.status_code == 404
    assert not (tmp_path / "jobs" / "pending" / "CTRL-TEST-2.json").exists()
