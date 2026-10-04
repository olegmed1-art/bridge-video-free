"""Linux-only resident crash/retention contracts, using synthetic inputs."""
import json
from pathlib import Path

import pytest
import requests

pytest.importorskip("fcntl")
from universal_video import spool_worker as worker
from universal_video.contract import canonical_job_hash, validate_job


@pytest.fixture
def spool(tmp_path, monkeypatch):
    payload = {"job_id": "synthetic-job", "profile": "transcript_only",
               "source": {"kind": "google_drive", "file_id": "synthetic_source_001"}}
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "synthetic-job.json").write_text(json.dumps(payload))
    media = tmp_path / "media"
    staged_dir = media / "drive-ready" / payload["job_id"]
    staged_dir.mkdir(parents=True)
    source = staged_dir / "source.mp4"
    source.write_bytes(b"synthetic")
    staged = {**payload, "source": {"kind": "oracle_drive_staged", "file_id": payload["source"]["file_id"],
              "path": str(source), "size_bytes": 1024 * 1024, "sha256": "b" * 64}}
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(media))
    monkeypatch.setattr(worker, "stage_drive_job", lambda *args: (staged, staged_dir))
    monkeypatch.setattr(worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(worker, "validate_staged_video", lambda path: None)
    monkeypatch.setattr(worker, "configured_binding", lambda *args: {"synthetic": True})
    monkeypatch.setattr(worker, "verify_result", lambda *args, **kw: {"state": "PASS"})
    monkeypatch.setattr(worker, "build_server_review", lambda *args: {"state": "PASS"})
    calls = []
    def run(payload, root):
        calls.append("compute")
        folder = root / payload["job_id"]
        folder.mkdir()
        result = {"job_id": payload["job_id"], "job_hash": canonical_job_hash(validate_job(payload, allowed_local_root=str(media))),
                  "status": "COMPLETED", "profile": "transcript_only", "media": {"sha256": "b" * 64}}
        (folder / "manifest.json").write_text(json.dumps(result))
        (folder / "transcript.txt").write_text("keep")
        return result
    monkeypatch.setattr(worker, "run_job", run)
    monkeypatch.setattr(worker, "cleanup_proof_matches", lambda *args, **kw: True)
    monkeypatch.setattr(worker, "remove_staged_job", lambda *args: calls.append("cleanup"))
    monkeypatch.setattr(worker, "finalize_drive_job", lambda *args, **kw: {"status": "PUBLISHED_VERIFIED"})
    return tmp_path, staged_dir, calls


def test_no_cleanup_when_upload_fails_and_finalization_retry_does_not_compute(spool, monkeypatch):
    root, source, calls = spool
    def fail(*args, **kwargs):
        raise requests.ConnectionError("synthetic upload failed")
    monkeypatch.setattr(worker, "finalize_drive_job", fail)
    assert worker.process_one(root)
    assert calls == ["compute"] and source.exists()
    assert (root / "inbox/synthetic-job.json").exists()
    assert not (root / "done/synthetic-job.json").exists()
    assert worker.process_one(root) is False  # bounded cooldown
    retry = root / "progress/synthetic-job.recovery.json"
    data = json.loads(retry.read_text())
    data["retry_after_unix"] = 0
    retry.write_text(json.dumps(data))
    monkeypatch.setattr(worker, "finalize_drive_job", lambda *args, **kw: {"status": "PUBLISHED_VERIFIED"})
    assert worker.process_one(root)
    assert calls == ["compute", "cleanup"]
    assert (root / "done/synthetic-job.json").exists()


@pytest.mark.parametrize("after_write", [False, True])
def test_interrupt_done_receipt_retains_source_and_orphan_recovers(spool, monkeypatch, after_write):
    root, source, calls = spool
    original = worker._atomic_write_json
    def interrupt(path, payload):
        if path.parent.name == "done":
            if after_write:
                original(path, payload)
            raise KeyboardInterrupt("synthetic crash at done receipt")
        original(path, payload)
    monkeypatch.setattr(worker, "_atomic_write_json", interrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.process_one(root)
    assert calls == ["compute"] and source.exists()
    assert worker.recover_orphaned_jobs(root)["recovered"] == 1
    retry = root / "progress/synthetic-job.recovery.json"
    data = json.loads(retry.read_text())
    data["retry_after_unix"] = 0
    retry.write_text(json.dumps(data))
    monkeypatch.setattr(worker, "_atomic_write_json", original)
    assert worker.process_one(root)
    assert calls == ["compute", "cleanup"]


def test_unknown_resident_auth_no_registry_retains_local_copy(spool, monkeypatch):
    root, source, calls = spool
    monkeypatch.setattr(worker, "configured_binding", lambda *args: None)
    monkeypatch.setattr(worker, "cleanup_proof_matches", lambda *args, **kw: False)
    monkeypatch.setattr(worker, "finalize_drive_job", lambda *args, **kw: pytest.fail("implicit upload"))
    assert worker.process_one(root)
    assert calls == ["compute"] and source.exists()


def test_retry_budget_exhaustion_retains_evidence(spool, monkeypatch):
    root, source, calls = spool
    monkeypatch.setattr(worker, "finalize_drive_job", lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError("synthetic")))
    for attempt in range(3):
        assert worker.process_one(root)
        retry = root / "progress/synthetic-job.recovery.json"
        data = json.loads(retry.read_text())
        data["retry_after_unix"] = 0
        retry.write_text(json.dumps(data))
    assert calls == ["compute"] and source.exists()
    assert (root / "results/synthetic-job/transcript.txt").read_text() == "keep"
    assert (root / "failed/synthetic-job.json").exists()
    assert not (root / "done/synthetic-job.json").exists()


def test_crash_before_manifest_preserves_partial_then_bounded_recovery(spool, monkeypatch):
    root, source, calls = spool
    original = worker.run_job
    def crash(payload, output):
        job = output / payload["job_id"]
        job.mkdir()
        (job / "partial-transcript.txt").write_text("unique partial evidence")
        raise KeyboardInterrupt("synthetic compute power loss")
    monkeypatch.setattr(worker, "run_job", crash)
    with pytest.raises(KeyboardInterrupt):
        worker.process_one(root)
    assert source.exists()
    assert worker.recover_orphaned_jobs(root)["recovered"] == 1
    monkeypatch.setattr(worker, "run_job", original)
    assert worker.process_one(root)
    preserved = list((root / "recovery/synthetic-job").glob("partial-*/partial-transcript.txt"))
    assert len(preserved) == 1 and preserved[0].read_text() == "unique partial evidence"
    assert calls == ["compute", "cleanup"]
