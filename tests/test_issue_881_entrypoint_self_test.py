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
