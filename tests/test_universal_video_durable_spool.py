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
    monkeypatch.setattr(worker, "queue_cleanup", lambda *args: tmp_path / "synthetic-intent.json")
    monkeypatch.setattr(worker, "retry_cleanup", lambda *args: calls.append("cleanup"))
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


def test_cleanup_intent_committed_before_running_claim_unlink(spool, monkeypatch):
    root, source, calls = spool
    def interrupt(spool_root, media, job_id, done):
        assert (root / "running/synthetic-job.json").exists()
        assert done.exists()
        (root / "synthetic-cleanup-intent.json").write_text("durable synthetic intent")
        raise KeyboardInterrupt("synthetic power loss after cleanup intent")
    monkeypatch.setattr(worker, "queue_cleanup", interrupt)
    with pytest.raises(KeyboardInterrupt):
        worker.process_one(root)
    assert (root / "synthetic-cleanup-intent.json").exists()
    assert (root / "running/synthetic-job.json").exists()
    assert source.exists() and calls == ["compute"]


def test_maintenance_cannot_enter_worker_exclusive_fence(tmp_path):
    import subprocess
    import sys
    import time
    from universal_video.workload_lock import shared_workload_lock
    spool = tmp_path / "spool"
    script = """
import sys
from pathlib import Path
from universal_video.maintenance import run_maintenance
base = Path(sys.argv[1])
(base / 'attempted').write_text('ready')
run_maintenance(base, dry_run=True)
(base / 'finished').write_text('done')
"""
    child = None
    try:
        with shared_workload_lock(spool, exclusive=True):
            child = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)])
            deadline = time.monotonic() + 5
            while not (tmp_path / "attempted").exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert (tmp_path / "attempted").exists()
            time.sleep(0.1)
            assert child.poll() is None and not (tmp_path / "finished").exists()
        assert child.wait(timeout=5) == 0
        assert (tmp_path / "finished").exists()
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=5)


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


@pytest.mark.parametrize("mode", ["missing", "invalid", "stripped-flag"])
def test_required_comparison_blocks_done_publication_and_cleanup(spool, monkeypatch, mode):
    root, source, calls = spool
    request_path = root / "inbox/synthetic-job.json"
    request = json.loads(request_path.read_text())
    request["metadata"] = {"comparison_required": True}
    request_path.write_text(json.dumps(request))
    old_stage, old_run = worker.stage_drive_job, worker.run_job
    def stage(job, payload, media):
        staged, directory = old_stage(job, payload, media)
        return {**staged, "metadata": payload["metadata"]}, directory
    def run(payload, output):
        result = old_run(payload, output)
        if mode != "stripped-flag":
            result["metadata"] = payload["metadata"]
        if mode == "invalid":
            result["comparison_artifacts"] = {}
        (output / payload["job_id"] / "manifest.json").write_text(json.dumps(result))
        return result
    monkeypatch.setattr(worker, "stage_drive_job", stage)
    monkeypatch.setattr(worker, "run_job", run)
    monkeypatch.setattr(worker, "finalize_drive_job", lambda *a, **kw: pytest.fail("publication bypass"))
    monkeypatch.setattr(worker, "build_server_review", lambda *a, **kw: pytest.fail("review bypass"))
    assert worker.process_one(root)
    assert calls == ["compute"] and source.exists()
    assert not (root / "done/synthetic-job.json").exists()
    assert (root / "failed/synthetic-job.json").exists()
    # Re-admission of retained completed evidence also cannot bypass the gate.
    request_path.write_text(json.dumps(request))
    assert worker.process_one(root)
    assert calls == ["compute"] and source.exists()
    assert not (root / "done/synthetic-job.json").exists()


def test_required_comparison_accepts_complete_package_before_review(spool, monkeypatch):
    from test_universal_video_comparison_artifacts import prepared, attach
    root, source, calls = spool
    request_path = root / "inbox/synthetic-job.json"
    request = json.loads(request_path.read_text())
    request["metadata"] = {"comparison_required": True}
    request_path.write_text(json.dumps(request))
    old_stage, old_run = worker.stage_drive_job, worker.run_job
    def stage(job, payload, media):
        staged, directory = old_stage(job, payload, media)
        return {**staged, "metadata": payload["metadata"]}, directory
    def run(payload, output):
        result = old_run(payload, output)
        result["metadata"] = payload["metadata"]
        attach(prepared(root, parent=(output / payload["job_id"], result)), with_review=False)
        return result
    monkeypatch.setattr(worker, "stage_drive_job", stage)
    monkeypatch.setattr(worker, "run_job", run)
    assert worker.process_one(root)
    assert calls == ["compute", "cleanup"]
    assert (root / "done/synthetic-job.json").exists()


@pytest.mark.parametrize("package", ["missing", "complete"])
@pytest.mark.parametrize("status", ["COMPLETED", "REVIEW", "FAILED"])
def test_required_job_without_drive_registry_still_refuses_completion(spool, monkeypatch, package, status):
    root, source, calls = spool
    request_path = root / "inbox/synthetic-job.json"
    request = json.loads(request_path.read_text())
    request["metadata"] = {"comparison_required": True}
    request_path.write_text(json.dumps(request))
    old_stage, old_run = worker.stage_drive_job, worker.run_job
    def stage(job, payload, media):
        staged, directory = old_stage(job, payload, media)
        return {**staged, "metadata": payload["metadata"]}, directory
    def run(payload, output):
        result = old_run(payload, output)
        result["metadata"] = payload["metadata"]
        result["status"] = status
        if package == "complete":
            from test_universal_video_comparison_artifacts import prepared, attach
            attach(prepared(root, parent=(output / payload["job_id"], result)), with_review=False)
        else:
            (output / payload["job_id"] / "manifest.json").write_text(json.dumps(result))
        return result
    monkeypatch.setattr(worker, "stage_drive_job", stage)
    monkeypatch.setattr(worker, "run_job", run)
    binding_calls = []
    def missing_binding(*args):
        binding_calls.append(args)
        return None
    monkeypatch.setattr(worker, "configured_binding", missing_binding)
    monkeypatch.setattr(worker, "finalize_drive_job", lambda *a, **k: pytest.fail("implicit publication"))
    assert worker.process_one(root)
    assert calls == ["compute"] and source.exists()
    assert (root / "failed/synthetic-job.json").exists()
    assert not (root / "done/synthetic-job.json").exists()
    assert len(binding_calls) == (1 if package == "complete" else 0)


@pytest.mark.parametrize("status", ["REVIEW", "FAILED"])
def test_ordinary_noncompleted_without_registry_keeps_local_receipt(spool, monkeypatch, status):
    root, source, calls = spool
    old_run = worker.run_job
    def run(payload, output):
        result = old_run(payload, output)
        result["status"] = status
        (output / payload["job_id"] / "manifest.json").write_text(json.dumps(result))
        return result
    monkeypatch.setattr(worker, "run_job", run)
    monkeypatch.setattr(worker, "configured_binding",
                        lambda *a: pytest.fail("ordinary noncompleted job requested Drive binding"))
    monkeypatch.setattr(worker, "finalize_drive_job",
                        lambda *a, **k: pytest.fail("ordinary noncompleted job published"))
    monkeypatch.setattr(worker, "cleanup_proof_matches", lambda *a, **k: False)
    assert worker.process_one(root)
    done = root / "done/synthetic-job.json"
    receipt = json.loads(done.read_text())
    assert receipt["compute_status"] == status
    assert receipt["result_conformance"]["state"] == "NOT_ELIGIBLE"
    assert source.exists() and calls == ["compute"]
    assert not (root / "failed/synthetic-job.json").exists()
