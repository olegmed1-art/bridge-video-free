from assistant_lab.control_bridge import BridgeConfig, _execute


def _config() -> BridgeConfig:
    return BridgeConfig(
        database_url="postgresql://example.invalid/neondb",
        control_token="t" * 48,
        worker_id="test-worker",
        poll_seconds=0,
    )


def test_execute_forwards_source_integrity(monkeypatch):
    calls = []

    def fake_request(config, method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/v1/run":
            return {"experiment_id": "CTRL-SOURCE-1"}
        return {"observer_report": {"exit_code": 0, "archive_status": "COPIED"}}

    monkeypatch.setattr("assistant_lab.control_bridge._request", fake_request)
    result = _execute(_config(), {
        "tool_id": "health.noop",
        "source_path": "/opt/bridge-school/assistant-lab-observer/sources/canary.bin",
        "source_sha256": "A" * 64,
        "experiment_id": "CTRL-SOURCE-1",
        "timeout_seconds": 30,
        "label": "source-contract",
    })

    payload = calls[0][2]
    assert payload["source_path"].endswith("/canary.bin")
    assert payload["source_sha256"] == "a" * 64
    assert result["experiment_id"] == "CTRL-SOURCE-1"


def test_execute_rejects_missing_source_before_http(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("HTTP must not be called for an invalid queue row")

    monkeypatch.setattr("assistant_lab.control_bridge._request", unexpected)
    try:
        _execute(_config(), {
            "tool_id": "health.noop",
            "source_path": None,
            "source_sha256": None,
        })
    except RuntimeError as exc:
        assert "source_path" in str(exc)
    else:
        raise AssertionError("missing source metadata must fail closed")


def test_execute_rejects_failed_observer_report(monkeypatch):
    def fake_request(config, method, path, payload=None):
        if path == "/v1/run":
            return {"experiment_id": "CTRL-FAILED-1"}
        return {"observer_report": {"exit_code": 1, "archive_status": "PENDING"}}

    monkeypatch.setattr("assistant_lab.control_bridge._request", fake_request)
    try:
        _execute(_config(), {
            "tool_id": "health.noop",
            "source_path": "/opt/bridge-school/assistant-lab-observer/sources/canary.bin",
            "source_sha256": "a" * 64,
            "experiment_id": "CTRL-FAILED-1",
            "timeout_seconds": 30,
            "label": "failed-observer",
        })
    except RuntimeError as exc:
        assert "exit_code=1" in str(exc)
    else:
        raise AssertionError("failed observer report must fail the control command")
