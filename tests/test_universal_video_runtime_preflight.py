from __future__ import annotations

import json
from unittest import mock

import pytest

from universal_video import runtime_preflight
from universal_video.runtime_preflight import VideoRuntimeUnavailable
from universal_video import spool_worker


def test_missing_ffmpeg_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_preflight.shutil, "which", lambda name: None if name == "ffmpeg" else f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_preflight.importlib.util, "find_spec", lambda name: object())
    with pytest.raises(VideoRuntimeUnavailable, match="VIDEO_RUNTIME_MISSING_TOOL:ffmpeg"):
        runtime_preflight.validate_video_runtime()


def test_missing_asr_is_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_preflight.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(runtime_preflight.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(VideoRuntimeUnavailable, match="VIDEO_RUNTIME_MISSING_ASR:faster_whisper"):
        runtime_preflight.validate_video_runtime()


def test_spool_preflight_fails_before_heavy_runner(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "job.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        spool_worker,
        "validate_video_runtime",
        mock.Mock(side_effect=VideoRuntimeUnavailable("VIDEO_RUNTIME_MISSING_TOOL:ffmpeg")),
    )
    runner = mock.Mock()
    monkeypatch.setattr(spool_worker, "run_job", runner)

    assert spool_worker.process_one(tmp_path) is True
    runner.assert_not_called()
    failure = json.loads((tmp_path / "failed" / "job.json").read_text(encoding="utf-8"))
    assert failure["status"] == "FAILED"
    assert failure["error_type"] == "VideoRuntimeUnavailable"
    assert "VIDEO_RUNTIME_MISSING_TOOL:ffmpeg" in failure["error"]
