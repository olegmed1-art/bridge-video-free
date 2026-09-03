from __future__ import annotations

import json

from universal_video import container_runtime


def test_entrypoint_self_test_is_bounded_and_skips_runtime_readiness(monkeypatch, capsys) -> None:
    def forbidden() -> dict[str, object]:
        raise AssertionError("entrypoint self-test must not touch runtime readiness")

    monkeypatch.setattr(container_runtime, "validate_container_runtime", forbidden)

    assert container_runtime.main([container_runtime.ENTRYPOINT_SELF_TEST]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt == {
        "schema": "universal-video-entrypoint-self-test-v1",
        "status": "PASS",
    }


def test_precanary_startup_probe_execs_exact_default_worker_command(monkeypatch) -> None:
    monkeypatch.setenv(container_runtime.PRECANARY_STARTUP_PROBE_ENV, "1")
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "a" * 40)
    monkeypatch.setenv("UNIVERSAL_VIDEO_STATUS_PATH", container_runtime.PRECANARY_STATUS_PATH)
    monkeypatch.setenv("UNIVERSAL_VIDEO_RESIDENT_ID", "container")
    for name in container_runtime.QUEUE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    observed: list[tuple[str, list[str]]] = []

    def capture(program: str, command: list[str]) -> None:
        observed.append((program, list(command)))
        raise SystemExit(0)

    monkeypatch.setattr(container_runtime.os, "execvp", capture)

    try:
        container_runtime.main(list(container_runtime.DEFAULT_WORKER_COMMAND))
    except SystemExit as exc:
        assert exc.code == 0

    assert observed == [("python", ["python", "-m", "universal_video.spool_worker"])]


def test_precanary_startup_probe_rejects_any_queue_configuration(monkeypatch, capsys) -> None:
    monkeypatch.setenv(container_runtime.PRECANARY_STARTUP_PROBE_ENV, "1")
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "b" * 40)
    monkeypatch.setenv("UNIVERSAL_VIDEO_STATUS_PATH", container_runtime.PRECANARY_STATUS_PATH)
    monkeypatch.setenv("UNIVERSAL_VIDEO_RESIDENT_ID", "container")
    monkeypatch.setenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", "/run/secrets/video-queue-dsn")

    assert container_runtime.main(list(container_runtime.DEFAULT_WORKER_COMMAND)) == 78
    failure = json.loads(capsys.readouterr().err)
    assert failure == {
        "error_code": "UV_CONTAINER_STARTUP_PROBE_QUEUE_FORBIDDEN",
        "status": "FAILED",
    }


def test_entrypoint_script_still_delegates_to_container_runtime() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    entrypoint = (
        root
        / "deploy"
        / "oracle-universal-video"
        / "universal-video-container-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "exec python -m universal_video.container_runtime \"$@\"" in entrypoint
