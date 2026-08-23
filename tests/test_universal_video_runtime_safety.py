import json
from pathlib import Path

import pytest

from universal_video.contract import (
    MAX_VIDEO_SECONDS,
    VideoContractError,
    canonical_job_hash,
    validate_job,
)
from universal_video.runner import _enforce_media_bounds, _prepare_job_dir, _qc_summary
from universal_video.spool_worker import recover_orphaned_jobs


def _job(tmp_path: Path, **overrides):
    source = tmp_path / "lesson.mp4"
    source.write_bytes(b"video")
    payload = {
        "job_id": "runtime-safety",
        "profile": "transcript_only",
        "source": {"kind": "local_path", "path": str(source)},
    }
    payload.update(overrides)
    return validate_job(payload, allowed_local_root=str(tmp_path))


def test_frame_interval_is_bounded(tmp_path: Path):
    with pytest.raises(VideoContractError, match="frame_interval_seconds"):
        _job(tmp_path, options={"frame_interval_seconds": 0})
    job = _job(tmp_path, options={"frame_interval_seconds": 120})
    assert job.options["frame_interval_seconds"] == 120


def test_source_size_option_is_bounded(tmp_path: Path):
    with pytest.raises(VideoContractError, match="max_source_bytes"):
        _job(tmp_path, options={"max_source_bytes": 1})
    job = _job(tmp_path, options={"max_source_bytes": 8 * 1024**3})
    assert job.options["max_source_bytes"] == 8 * 1024**3


def test_hard_duration_limit_applies_even_without_job_override(tmp_path: Path):
    job = _job(tmp_path)
    media = {"duration_seconds": MAX_VIDEO_SECONDS + 1, "size_bytes": 100}
    with pytest.raises(RuntimeError, match="hard duration"):
        _enforce_media_bounds(media, job, source_limit=1024)


def test_job_duration_and_source_size_limits_are_enforced(tmp_path: Path):
    job = _job(tmp_path, options={"max_duration_seconds": 60})
    with pytest.raises(RuntimeError, match="max_duration_seconds"):
        _enforce_media_bounds({"duration_seconds": 61, "size_bytes": 100}, job, source_limit=1024)
    with pytest.raises(RuntimeError, match="source-size"):
        _enforce_media_bounds({"duration_seconds": 30, "size_bytes": 1025}, job, source_limit=1024)


def test_short_recording_cannot_pass_with_its_only_qc_block_failed():
    transcript = [{"text": "some words"}]
    passed, failed, allowed = _qc_summary(transcript, [{"ok": False}])
    assert passed is False
    assert failed == 1
    assert allowed == 0


def test_twenty_percent_qc_tolerance_starts_at_five_blocks():
    transcript = [{"text": "some words"}]
    passed, failed, allowed = _qc_summary(transcript, [{"ok": False}] + [{"ok": True}] * 4)
    assert passed is True
    assert failed == 1
    assert allowed == 1


def test_completed_same_hash_is_reused_without_touching_artifacts(tmp_path: Path):
    job = _job(tmp_path)
    output_root = tmp_path / "output"
    job_dir = output_root / job.job_id
    job_dir.mkdir(parents=True)
    artifact = job_dir / "transcript.txt"
    artifact.write_text("canonical transcript", encoding="utf-8")
    expected = {
        "status": "COMPLETED",
        "job_hash": canonical_job_hash(job),
        "job_id": job.job_id,
    }
    (job_dir / "manifest.json").write_text(json.dumps(expected), encoding="utf-8")

    prepared, job_hash, existing = _prepare_job_dir(output_root, job)

    assert prepared == job_dir
    assert job_hash == expected["job_hash"]
    assert existing == expected
    assert artifact.read_text(encoding="utf-8") == "canonical transcript"


def test_changed_job_hash_cleans_stale_output(tmp_path: Path):
    job = _job(tmp_path)
    output_root = tmp_path / "output"
    stale = output_root / job.job_id
    stale.mkdir(parents=True)
    (stale / "old-frame.jpg").write_bytes(b"old")
    (stale / "manifest.json").write_text(
        json.dumps({"status": "COMPLETED", "job_hash": "different"}),
        encoding="utf-8",
    )
    job_dir, _, existing = _prepare_job_dir(output_root, job)
    assert existing is None
    assert job_dir.is_dir()
    assert not (job_dir / "old-frame.jpg").exists()


def test_spool_startup_recovers_orphaned_running_job(tmp_path: Path):
    running = tmp_path / "running"
    running.mkdir(parents=True)
    payload = running / "job.json"
    payload.write_text('{"job_id":"x"}', encoding="utf-8")
    result = recover_orphaned_jobs(tmp_path)
    assert result == {"recovered": 1, "deduplicated": 0, "conflicts": 0}
    assert (tmp_path / "inbox" / "job.json").exists()
    assert not payload.exists()


def test_spool_recovery_deduplicates_identical_inbox_payload(tmp_path: Path):
    running = tmp_path / "running"
    inbox = tmp_path / "inbox"
    running.mkdir(parents=True)
    inbox.mkdir(parents=True)
    (running / "job.json").write_text('{"job_id":"x"}', encoding="utf-8")
    (inbox / "job.json").write_text('{"job_id":"x"}', encoding="utf-8")
    result = recover_orphaned_jobs(tmp_path)
    assert result == {"recovered": 0, "deduplicated": 1, "conflicts": 0}
    assert not (running / "job.json").exists()
