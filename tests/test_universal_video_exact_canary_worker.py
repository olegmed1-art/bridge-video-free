from __future__ import annotations

import sys
import types
from contextlib import nullcontext

import pytest

from universal_video import exact_canary_worker as subject
from universal_video import neon_worker
from universal_video.drive_result_readback import DriveResultContractError


def _claim():
    return {
        "job_id": "11111111-1111-1111-1111-111111111111",
        "batch_id": "22222222-2222-2222-2222-222222222222",
        "lease_token": "33333333-3333-3333-3333-333333333333",
        "sequence": 1,
        "source_folder_id": "sourcefolder0001",
        "output_folder_id": "outputfolder0001",
        "work_folder_id": "workfolder000001",
        "processing_profile": subject.EXACT_CANARY_PROFILE,
        "algorithm_revision": subject.APPROVED_REVISION,
        "source_file_id": "source00000001",
        "source_name": "Lesson 13.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 123,
        "source_checksum": "md5:" + "a" * 32,
        "stable_job_key": "job000000000001",
        "is_canary": True,
        "attempt_count": 1,
    }


def _runtime(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "1" * 40)
    monkeypatch.setenv("UNIVERSAL_VIDEO_IMAGE_DIGEST", "sha256:" + "2" * 64)


def test_source_is_rechecked_immediately_before_processing(monkeypatch):
    _runtime(monkeypatch)
    events = []
    monkeypatch.setattr(subject, "access_token", lambda: "token")
    monkeypatch.setattr(subject, "verify_claimed_source_identity", lambda *_: events.append("source") or {
        "file_id": "source00000001", "name": "Lesson 13.mp4", "mime_type": "video/mp4",
        "size_bytes": 123, "parent_id": "sourcefolder0001", "checksum": "md5:" + "a" * 32})
    monkeypatch.setattr(subject, "_stable_environment", lambda _claim: nullcontext())
    monkeypatch.setitem(sys.modules, "bridge_runtime_hardening_r25_16", types.SimpleNamespace(
        run=lambda _token: events.append("processing") or {
            "status": "AI_DONE", "job_id": "job000000000001",
            "algorithmRevision": subject.APPROVED_REVISION,
            "original": {"driveId": "source00000001"},
            "masterPdf": {"driveId": "master000000001", "sha256": "3" * 64}}))
    monkeypatch.setitem(sys.modules, "route_drive_job_outputs", types.SimpleNamespace(
        main=lambda: events.append("routing") or 0))
    monkeypatch.setattr(subject, "verify_routed_result_contract", lambda *_: events.append("readback") or {
        "terminal_receipt": {"status": "REVIEW_READY"}})
    result = subject.strict_review_processor(_claim())
    assert events == ["source", "processing", "routing", "readback"]
    assert result["source_recheck_stage"] == "IMMEDIATELY_BEFORE_PROCESSING"


def test_worker_claims_only_isolated_profile_once(monkeypatch):
    _runtime(monkeypatch)
    monkeypatch.setattr(subject, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(subject, "database_url_from_env", lambda: "dsn")
    monkeypatch.setattr(subject, "worker_key_from_env", lambda: "worker-1")
    observed = {}
    monkeypatch.setattr(subject, "claim_job", lambda _dsn, _key, **kwargs: observed.update(kwargs) or _claim())
    monkeypatch.setattr(subject, "process_claim", lambda *_args, **_kwargs: {
        "job_status": "REVIEW_READY", "batch_status": "REVIEW", "released_jobs": 0})
    result = subject.process_exactly_one()
    assert observed["processing_profile"] == subject.EXACT_CANARY_PROFILE
    assert result["claims_processed"] == 1
    assert result["resident_loop_entered"] is False
    assert result["exactly_one_gate"] == "PASS"


def test_missing_job_is_blocked(monkeypatch):
    _runtime(monkeypatch)
    monkeypatch.setattr(subject, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(subject, "database_url_from_env", lambda: "dsn")
    monkeypatch.setattr(subject, "worker_key_from_env", lambda: "worker-1")
    monkeypatch.setattr(subject, "claim_job", lambda *_args, **_kwargs: None)
    with pytest.raises(subject.ExactCanaryWorkerError, match="JOB_NOT_FOUND"):
        subject.process_exactly_one()


def test_any_released_job_is_blocked(monkeypatch):
    _runtime(monkeypatch)
    monkeypatch.setattr(subject, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(subject, "database_url_from_env", lambda: "dsn")
    monkeypatch.setattr(subject, "worker_key_from_env", lambda: "worker-1")
    monkeypatch.setattr(subject, "claim_job", lambda *_args, **_kwargs: _claim())
    monkeypatch.setattr(subject, "process_claim", lambda *_args, **_kwargs: {
        "job_status": "REVIEW_READY", "batch_status": "RUNNING", "released_jobs": 1})
    with pytest.raises(subject.ExactCanaryWorkerError, match="RELEASED_ADDITIONAL"):
        subject.process_exactly_one()


class NoHeartbeat:
    error = None
    def __init__(self, *_args, **_kwargs): pass
    def __enter__(self): return self
    def __exit__(self, *_args): return None


def test_failed_readback_retries_and_never_finishes_pass(monkeypatch):
    monkeypatch.setattr(neon_worker, "_Heartbeat", NoHeartbeat)
    monkeypatch.setattr(neon_worker, "_processing_timeout", nullcontext)
    retried = {}
    monkeypatch.setattr(neon_worker, "retry_job", lambda _dsn, **kwargs: retried.update(kwargs) or {
        "job_status": "QUEUED", "batch_status": "RUNNING"})
    monkeypatch.setattr(neon_worker, "finish_job", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("false PASS")))

    def failed(_claim):
        raise DriveResultContractError("MEDIA_READBACK_FAILED")

    result = neon_worker.process_claim("dsn", _claim(), "worker-1", processor=failed)
    assert result["job_status"] == "QUEUED"
    assert retried["error_code"] == "UV_DRIVE_READBACK_FAILED"
