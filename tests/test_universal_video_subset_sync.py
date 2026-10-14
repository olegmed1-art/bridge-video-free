"""The opt-in resident must use the restricted synchronous subset path."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import sys
from types import SimpleNamespace

import pytest

if sys.platform == "win32":
    # The resident runs on Linux. Import-only stand-in for its workload lock.
    sys.modules.setdefault("fcntl", SimpleNamespace())

from universal_video import neon_worker
from universal_video import subset_drive_observation as observation
from universal_video.subset_drive_observation import (
    DriveSubsetObservationProvider, SubsetDriveConfig,
    SubsetDriveObservationError, subset_sync_enabled,
)


def test_mode_and_target_binding(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SUBSET_SYNC", "yes")
    with pytest.raises(SubsetDriveObservationError, match="VIDEO_SUBSET_MODE_INVALID"):
        subset_sync_enabled()
    cfg = SubsetDriveConfig("approval", "batch",
        "postgresql://collector@127.0.0.1:5432/one", "127.0.0.1", "one")
    cfg.validate_target("postgresql://worker@127.0.0.1:5432/one")
    with pytest.raises(SubsetDriveObservationError, match="VIDEO_SUBSET_TARGET_MISMATCH"):
        cfg.validate_target("postgresql://worker@127.0.0.1:5432/other")
    remote = SubsetDriveConfig("approval", "batch",
        "postgresql://collector@example.neon.tech/neondb", "example.neon.tech", "neondb")
    with pytest.raises(SubsetDriveObservationError, match="VIDEO_SUBSET_TLS_BINDING_REQUIRED"):
        remote.validate_target("postgresql://worker@example.neon.tech/neondb")


def test_wrong_batch_rejected_before_drive_or_sql():
    touched = []
    provider = DriveSubsetObservationProvider(
        SubsetDriveConfig("approval", "expected", "unused", "host", "db"),
        "worker", token_source=lambda: touched.append("token"),
        metadata_fetcher=lambda *_: touched.append("drive"))
    with pytest.raises(SubsetDriveObservationError, match="VIDEO_SUBSET_CLAIM_MISMATCH"):
        provider({"batch_id": "other", "source_file_id": "file",
                  "job_id": "job", "lease_token": "lease", "attempt_count": 1},
                 "worker", boundary="before_work")
    assert touched == []


def test_optin_claim_is_scoped_and_legacy_call_unchanged(monkeypatch):
    calls = []
    dsn = "postgresql://worker@127.0.0.1:5432/one"
    config = SubsetDriveConfig("approval", "target-batch",
        "postgresql://collector@127.0.0.1:5432/one", "127.0.0.1", "one")
    monkeypatch.setattr(neon_worker, "shared_workload_lock", nullcontext)
    monkeypatch.setattr(neon_worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(neon_worker, "database_url_from_env", lambda: dsn)
    monkeypatch.setattr(neon_worker, "worker_key_from_env", lambda: "worker")
    monkeypatch.setattr(neon_worker, "claim_job",
        lambda *_a, **kwargs: calls.append(kwargs) or None)
    monkeypatch.setattr(observation.SubsetDriveConfig, "from_env",
        classmethod(lambda cls: config))
    monkeypatch.setattr(observation, "restricted_identity",
        lambda _dsn, capability: "worker" if capability == "video_subset_worker_v2"
        else "collector")

    monkeypatch.setenv("UNIVERSAL_VIDEO_SUBSET_SYNC", "1")
    assert neon_worker.process_one_neon() is False
    assert calls[-1]["batch_id"] == "target-batch"

    monkeypatch.setenv("UNIVERSAL_VIDEO_SUBSET_SYNC", "0")
    assert neon_worker.process_one_neon() is False
    assert "batch_id" not in calls[-1]


def test_synchronous_observations_bracket_processor(monkeypatch):
    calls = []
    claim = {"job_id": "job", "lease_token": "lease", "source_file_id": "file",
             "stable_job_key": "stable",
             "algorithm_revision": neon_worker.APPROVED_REVISION}

    @contextmanager
    def no_timeout():
        yield

    @contextmanager
    def no_heartbeat(*_args):
        yield SimpleNamespace(error=None)

    monkeypatch.setattr(neon_worker, "_processing_timeout", no_timeout)
    monkeypatch.setattr(neon_worker, "_Heartbeat", no_heartbeat)
    monkeypatch.setattr(neon_worker, "access_token", lambda: "local")
    monkeypatch.setattr(neon_worker, "verify_claimed_source",
                        lambda *_: {"revision": "same"})
    monkeypatch.setattr(neon_worker, "verify_terminal_output_live",
                        lambda *_: calls.append("readback"))
    monkeypatch.setattr(neon_worker, "finish_job",
                        lambda *_a, **_k: pytest.fail("legacy finish called"))
    monkeypatch.setattr(neon_worker, "retry_job",
                        lambda *_a, **_k: pytest.fail("legacy retry called"))

    class Adapter:
        def before_work(self, *_args):
            calls.append("before_work")
            return "pre"

        def finish_job(self, *_args, **kwargs):
            calls.append("finish")
            assert kwargs["before_work_observation_id"] == "pre"
            assert kwargs["outcome"] == "REVIEW_READY"
            return {"job_status": "REVIEW_READY"}

        def retry_job(self, *_args, **_kwargs):
            calls.append("retry")
            return {"job_status": "QUEUED"}

    result = neon_worker.process_claim(
        "local", claim, "worker", subset_adapter=Adapter(),
        processor=lambda *_: calls.append("processor") or {"artifact": "synthetic"})
    assert result == {"job_status": "REVIEW_READY"}
    assert calls == ["before_work", "processor", "readback", "finish"]

    calls.clear()
    result = neon_worker.process_claim(
        "local", claim, "worker", subset_adapter=Adapter(),
        processor=lambda *_: (_ for _ in ()).throw(RuntimeError("failed")))
    assert result == {"job_status": "QUEUED"}
    assert calls == ["before_work", "retry"]
