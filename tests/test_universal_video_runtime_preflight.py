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


def test_spool_receipt_has_locator_and_generation_conformance(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "media" / "lesson.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    payload = {
        "job_id": "receipt-job",
        "profile": "transcript_only",
        "source": {"kind": "local_path", "path": str(source)},
    }
    (inbox / "receipt-job.json").write_text(json.dumps(payload), encoding="utf-8")

    def fake_run(_payload, results):
        result_dir = results / "receipt-job"
        result_dir.mkdir(parents=True)
        result = {
            "status": "COMPLETED",
            "job_id": "receipt-job",
            "job_hash": "a" * 64,
            "profile": "transcript_only",
            "source": {"kind": "local_path"},
            "media": {"size_bytes": 5, "duration_seconds": 1.0},
            "runtime": {"elapsed_seconds": 2.0},
        }
        (result_dir / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
        return result

    conformance = {
        "schema": "universal-video-result-conformance-v1",
        "state": "PASS",
        "evidence_phase": "GENERATION_FINALIZATION",
        "artifact_set_sha256": "b" * 64,
    }
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(source.parent))
    monkeypatch.setattr(spool_worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(spool_worker, "run_job", fake_run)
    verifier = mock.Mock(return_value=conformance)
    monkeypatch.setattr(spool_worker, "verify_result", verifier)

    assert spool_worker.process_one(tmp_path) is True
    receipt = json.loads((tmp_path / "done" / "receipt-job.json").read_text(encoding="utf-8"))
    expected_dir = tmp_path / "results" / "receipt-job"
    assert receipt["compute_status"] == "COMPLETED"
    assert receipt["result_dir"] == str(expected_dir)
    assert receipt["result_locator"] == {"kind": "local_directory", "path": str(expected_dir)}
    assert receipt["result_conformance"] == conformance
    verifier.assert_called_once()


def test_review_receipt_is_not_marked_technical_ready(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "media" / "lesson.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    payload = {
        "job_id": "review-job",
        "profile": "transcript_only",
        "source": {"kind": "local_path", "path": str(source)},
    }
    (inbox / "review-job.json").write_text(json.dumps(payload), encoding="utf-8")

    def fake_run(_payload, results):
        result_dir = results / "review-job"
        result_dir.mkdir(parents=True)
        result = {
            "status": "REVIEW",
            "job_id": "review-job",
            "profile": "transcript_only",
            "source": {"kind": "local_path"},
            "media": {"size_bytes": 5, "duration_seconds": 1.0},
            "runtime": {"elapsed_seconds": 2.0},
        }
        (result_dir / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(source.parent))
    monkeypatch.setattr(spool_worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(spool_worker, "run_job", fake_run)
    verifier = mock.Mock()
    monkeypatch.setattr(spool_worker, "verify_result", verifier)

    assert spool_worker.process_one(tmp_path) is True
    receipt = json.loads((tmp_path / "done" / "review-job.json").read_text(encoding="utf-8"))
    assert receipt["compute_status"] == "REVIEW"
    assert receipt["result_conformance"]["state"] == "NOT_ELIGIBLE"
    assert receipt["result_conformance"]["technical_bundle_ready"] is False
    verifier.assert_not_called()
