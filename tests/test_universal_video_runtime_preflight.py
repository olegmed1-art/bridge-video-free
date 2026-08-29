from __future__ import annotations

import json
import hashlib
from unittest import mock
from pathlib import Path

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
    assert failure["error_code"] == "UV_RUNTIME_DEPENDENCY_MISSING"
    assert "error" not in failure


def test_spool_failure_receipt_drops_exception_text(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "media" / "lesson.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    payload = {
        "job_id": "failure-boundary",
        "profile": "transcript_only",
        "source": {"kind": "local_path", "path": str(source)},
    }
    (inbox / "failure-boundary.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(source.parent))
    monkeypatch.setattr(spool_worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(
        spool_worker,
        "run_job",
        mock.Mock(side_effect=RuntimeError("DO_NOT_PUBLISH_RAW_STDERR /private/source-name.mp4")),
    )

    assert spool_worker.process_one(tmp_path) is True
    receipt_text = (tmp_path / "failed" / "failure-boundary.json").read_text(encoding="utf-8")
    failure = json.loads(receipt_text)
    assert failure["error_type"] == "RuntimeError"
    assert failure["error_code"] == "UV_WORKER_RUNTIME_FAILED"
    assert "error" not in failure
    assert "DO_NOT_PUBLISH_RAW_STDERR" not in receipt_text
    assert "source-name.mp4" not in receipt_text


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
    review = {
        "schema": "universal-video-server-review-v1",
        "state": "PASS",
        "handoff": {"mode": "SUMMARY_ONLY"},
    }
    review_builder = mock.Mock(return_value=review)
    monkeypatch.setattr(spool_worker, "build_server_review", review_builder)

    assert spool_worker.process_one(tmp_path) is True
    receipt = json.loads((tmp_path / "done" / "receipt-job.json").read_text(encoding="utf-8"))
    expected_dir = tmp_path / "results" / "receipt-job"
    assert receipt["compute_status"] == "COMPLETED"
    assert receipt["result_dir"] == str(expected_dir)
    assert receipt["result_locator"] == {"kind": "local_directory", "path": str(expected_dir)}
    assert receipt["result_conformance"] == conformance
    assert verifier.call_count == 2
    assert verifier.call_args_list[1].kwargs["require_server_review"] is True
    review_builder.assert_called_once_with(expected_dir, conformance)
    assert json.loads((expected_dir / "server_review.json").read_text(encoding="utf-8")) == review


def test_spool_reuse_revalidates_existing_server_review_without_rewriting(tmp_path, monkeypatch):
    source = tmp_path / "media" / "lesson.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    payload = {
        "job_id": "reuse-job",
        "profile": "transcript_only",
        "source": {"kind": "local_path", "path": str(source)},
    }
    (inbox / "reuse-job.json").write_text(json.dumps(payload), encoding="utf-8")

    existing_review = {"schema": "universal-video-server-review-v1", "state": "PASS"}

    def fake_run(_payload, results):
        result_dir = results / "reuse-job"
        result_dir.mkdir(parents=True)
        result = {
            "status": "COMPLETED",
            "job_id": "reuse-job",
            "job_hash": "a" * 64,
            "profile": "transcript_only",
            "source": {"kind": "local_path"},
            "media": {"size_bytes": 5, "duration_seconds": 1.0},
            "runtime": {"elapsed_seconds": 2.0},
            "finops_observation": {"schema": "universal-video-finops-v1"},
        }
        (result_dir / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
        (result_dir / "server_review.json").write_text(json.dumps(existing_review), encoding="utf-8")
        return result

    conformance = {"schema": "universal-video-result-conformance-v1", "state": "PASS"}
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(source.parent))
    monkeypatch.setattr(spool_worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(spool_worker, "run_job", fake_run)
    verifier = mock.Mock(return_value=conformance)
    review_builder = mock.Mock()
    monkeypatch.setattr(spool_worker, "verify_result", verifier)
    monkeypatch.setattr(spool_worker, "build_server_review", review_builder)

    assert spool_worker.process_one(tmp_path) is True
    verifier.assert_called_once()
    assert verifier.call_args.kwargs["evidence_phase"] == "REUSE_OBSERVATION"
    assert verifier.call_args.kwargs["require_server_review"] is True
    review_builder.assert_not_called()
    review_path = tmp_path / "results" / "reuse-job" / "server_review.json"
    assert json.loads(review_path.read_text(encoding="utf-8")) == existing_review


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


def test_drive_job_is_staged_on_oracle_before_processing_and_then_removed(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    media = tmp_path / "media"
    media.mkdir()
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    payload = {
        "job_id": "drive-chain-job",
        "profile": "transcript_only",
        "source": {"kind": "google_drive", "file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz"},
    }
    (inbox / "drive-chain-job.json").write_text(json.dumps(payload), encoding="utf-8")

    def fake_stage(job, original, media_root):
        job_dir = media_root / "drive-ready" / job.job_id
        job_dir.mkdir(parents=True)
        source = job_dir / "source.mp4"
        source.write_bytes(b"v" * (1024 * 1024))
        staged = dict(original)
        staged["source"] = {
            "kind": "oracle_drive_staged",
            "path": str(source),
            "file_id": payload["source"]["file_id"],
            "size_bytes": source.stat().st_size,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        return staged, job_dir

    def fake_run(staged, results):
        assert staged["source"]["kind"] == "oracle_drive_staged"
        assert Path(staged["source"]["path"]).exists()
        result_dir = results / "drive-chain-job"
        result_dir.mkdir(parents=True)
        result = {
            "status": "REVIEW",
            "job_id": "drive-chain-job",
            "profile": "transcript_only",
            "source": {"kind": "google_drive", "file_id": payload["source"]["file_id"]},
            "media": {"size_bytes": 1024 * 1024, "duration_seconds": 1.0},
            "runtime": {"elapsed_seconds": 2.0},
        }
        (result_dir / "manifest.json").write_text(json.dumps(result), encoding="utf-8")
        return result

    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(media))
    monkeypatch.setenv("UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE", "1")
    monkeypatch.setattr(spool_worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(spool_worker, "stage_drive_job", fake_stage)
    monkeypatch.setattr(spool_worker, "run_job", fake_run)
    assert spool_worker.process_one(tmp_path) is True
    assert not (media / "drive-ready" / "drive-chain-job").exists()
    progress = json.loads((tmp_path / "progress" / "drive-chain-job.json").read_text(encoding="utf-8"))
    assert progress["state"] == "REVIEW"
