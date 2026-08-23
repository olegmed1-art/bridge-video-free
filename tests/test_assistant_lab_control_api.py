import hashlib
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
    source_root = tmp_path / "sources"
    source_root.mkdir()
    source = source_root / "canary-video.bin"
    source.write_bytes(b"explicit-canary-video")
    monkeypatch.setenv("ASSISTANT_LAB_OBSERVER_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("ASSISTANT_LAB_CONTROL_REGISTRY", str(registry))
    monkeypatch.setenv("ASSISTANT_LAB_CONTROL_TOKEN", token)
    return token, source, hashlib.sha256(source.read_bytes()).hexdigest()


def test_health_is_authenticated_and_fail_closed(monkeypatch, tmp_path):
    token, _, _ = _configure(monkeypatch, tmp_path)
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
    token, source, digest = _configure(monkeypatch, tmp_path)
    result = run(
        RunRequest(
            tool_id="health.noop", source_path=str(source), source_sha256=digest,
            experiment_id="CTRL-TEST-1", timeout_seconds=30, label="control-test",
        ),
        f"Bearer {token}",
    )
    assert result["status"] == "queued"
    assert result["experiment_id"] == "CTRL-TEST-1"
    job = json.loads((tmp_path / "jobs" / "pending" / "CTRL-TEST-1.json").read_text(encoding="utf-8"))
    assert job["command"] == ["/bin/true"]
    assert job["env"] == {}
    assert job["source"] == {"path": str(source), "sha256": digest}


def test_unknown_tool_cannot_become_arbitrary_command(monkeypatch, tmp_path):
    token, source, digest = _configure(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc:
        run(RunRequest(tool_id="bash", source_path=str(source), source_sha256=digest,
                       experiment_id="CTRL-TEST-2"), f"Bearer {token}")
    assert exc.value.status_code == 404
    assert not (tmp_path / "jobs" / "pending" / "CTRL-TEST-2.json").exists()
