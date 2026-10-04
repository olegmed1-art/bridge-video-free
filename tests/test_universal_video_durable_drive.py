"""Synthetic Drive protocol tests. No credentials, real media or live receipts."""
import hashlib
import json
import re
import os
from pathlib import Path

import pytest
import requests

from universal_video import drive_adapter as adapter, drive_results as outputs, durable_drive as durable


SOURCE = "synthetic_original_01"
CONTENT = b"synthetic source"
SHA = hashlib.sha256(CONTENT).hexdigest()
ROLES = ("school_root", "processing", "video", "source_job", "transcript", "frames", "analysis", "checks")
FOLDERS = {role: f"synthetic_{role}_01" for role in ROLES}
META = {"id": SOURCE, "name": "generic.mp4", "mimeType": "video/mp4", "size": str(len(CONTENT)),
        "version": "1", "modifiedTime": "2026-01-01T00:00:00Z", "parents": ["original_folder"], "trashed": False}


class Response:
    def __init__(self, data=None, content=b"", error=None):
        self.data, self.content, self.error = data, content, error
    def json(self):
        return self.data
    def raise_for_status(self):
        if self.error:
            raise self.error
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]


class FakeDrive:
    def __init__(self):
        self.meta = dict(META)
        self.files = {}
        self.posts = []
        self.corrupt = False
        self.fail_upload = False
        self.fail_readback = False
        self.reparent = False
    def get(self, url, **kwargs):
        params = kwargs.get("params", {})
        file_id = url.rsplit("/", 1)[-1]
        if "q" in params:
            q = params["q"]
            parent = re.search(r"'([^']+)' in parents", q).group(1)
            match = re.search(r"name = '([^']+)'", q)
            name = match.group(1) if match else None
            return Response({"files": [dict(v) for v in self.files.values()
                                       if parent in v["parents"] and (name is None or v["name"] == name)]})
        if file_id == SOURCE:
            return Response(content=CONTENT) if params.get("alt") == "media" else Response(dict(self.meta))
        if file_id in FOLDERS.values():
            role = next(k for k, v in FOLDERS.items() if v == file_id)
            parent_role = {"processing": "school_root", "video": "processing", "source_job": "video"}.get(role, "source_job")
            return Response({"id": file_id, "mimeType": outputs.FOLDER_MIME, "trashed": False,
                             "parents": [] if role == "school_root" else [FOLDERS[parent_role]],
                             "capabilities": {"canAddChildren": True}, "permissions": [{"type": "user", "role": "owner"}]})
        remote = dict(self.files[file_id])
        if params.get("alt") == "media":
            if self.fail_readback:
                raise requests.ConnectionError("synthetic read-back interruption")
            return Response(content=b"wrong" if self.corrupt else remote["content"])
        if self.reparent:
            remote["parents"] = ["wrong_folder"]
        return Response(remote)
    def post(self, url, **kwargs):
        if self.fail_upload:
            raise requests.ConnectionError("synthetic upload interruption")
        assert url == outputs.UPLOAD  # never source files endpoint / PATCH / DELETE
        boundary = kwargs["headers"]["Content-Type"].split("boundary=")[1].encode()
        parts = kwargs["data"].split(b"--" + boundary)
        meta = json.loads(parts[1].split(b"\r\n\r\n", 1)[1][:-2])
        content = parts[2].split(b"\r\n\r\n", 1)[1][:-2]
        file_id = f"output_{len(self.files) + 1}"
        self.files[file_id] = {**meta, "id": file_id, "size": str(len(content)), "content": content,
                               "md5Checksum": hashlib.md5(content, usedforsecurity=False).hexdigest(),
                               "permissions": [{"type": "user", "role": "owner"}], "trashed": False, "version": "1"}
        self.posts.append((url, meta))
        return Response({"id": file_id})


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    durable.atomic_json(source_dir / durable.SOURCE_RECEIPT,
                        {"schema": "universal-video-source-integrity-v1", "job_id": "job", "job_hash": "a" * 64,
                         "original": adapter.original_snapshot(META), "sha256": SHA})
    result = tmp_path / "job"
    result.mkdir()
    manifest = {"status": "COMPLETED", "job_id": "job", "job_hash": "a" * 64,
                "profile": "transcript_only", "media": {"sha256": SHA}}
    (result / "manifest.json").write_text(json.dumps(manifest))
    for name in ("transcript.txt", "transcript.jsonl", "transcript_qc.json"):
        (result / name).write_text("synthetic output")
    (result / "frames").mkdir()
    (result / "frames/frame.jpg").write_bytes(b"synthetic frame")
    backend = FakeDrive()
    monkeypatch.setattr(adapter.requests, "get", backend.get)
    monkeypatch.setattr(adapter.requests, "post", backend.post)
    monkeypatch.setattr(durable, "access_token", lambda: "synthetic-token")
    monkeypatch.setattr(durable, "verify_result", lambda *args, **kw: {"state": "PASS", "artifact_set_sha256": kw["expected_artifact_set_sha256"]})
    binding = {"source_file_id": SOURCE, "folders": dict(FOLDERS)}
    return result, source_dir, backend, binding


def finish(setup):
    result, source, _, binding = setup
    return durable.finalize_drive_job(result, source, binding, job_id="job", profile="transcript_only", job_hash="a" * 64)


def test_shared_inventory_uses_realistic_list_field_selection(setup, monkeypatch):
    result, _, drive, _ = setup
    artifact = outputs.collect_compact_artifacts(result)[0]
    outputs._upload_or_verify_file(FOLDERS["checks"], artifact, "synthetic-token")
    original = drive.get
    def selected(url, **kw):
        response = original(url, **kw)
        if "q" in kw.get("params", {}):
            fields = kw["params"]["fields"]
            # Drive list projection really omits fields not requested.
            response.data["files"] = [{k: v for k, v in item.items()
                                       if re.search(r"\b" + k + r"\b", fields)}
                                      for item in response.data["files"]]
        return response
    monkeypatch.setattr(adapter.requests, "get", selected)
    records = outputs._verify_remote_inventory(FOLDERS["checks"], [artifact], "synthetic-token")
    assert records[0]["verification"] == durable.READBACK
    drive.reparent = True
    with pytest.raises(RuntimeError):
        outputs._verify_remote_inventory(FOLDERS["checks"], [artifact], "synthetic-token")


@pytest.fixture
def full_chain(setup, tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("stage directory fsync is a Linux resident contract")
    from universal_video import drive_stage as stage
    from universal_video.contract import validate_job, canonical_job_hash
    monkeypatch.setitem(globals(), "CONTENT", b"v" * (1024 * 1024))
    monkeypatch.setitem(globals(), "SHA", hashlib.sha256(CONTENT).hexdigest())
    result, _, drive, binding = setup
    drive.meta["size"] = str(len(CONTENT))
    media = tmp_path / "media"
    media.mkdir()
    payload = {"job_id": "job", "profile": "transcript_only", "source": {"kind": "google_drive", "file_id": SOURCE}}
    job = validate_job(payload)
    job_hash = canonical_job_hash(job)
    monkeypatch.setattr(stage, "access_token", lambda: "synthetic-token")
    staged, source = stage.stage_drive_job(job, payload, media)
    manifest = durable.read_receipt(result / "manifest.json")
    manifest.update(job_hash=job_hash, media={"sha256": SHA})
    durable.atomic_json(result / "manifest.json", manifest)
    def finalize():
        return durable.finalize_drive_job(result, source, binding, job_id="job", profile="transcript_only", job_hash=job_hash)
    return job, payload, media, source, staged, drive, result, finalize


@pytest.mark.parametrize("mutation,cache_loss", [("name", False), ("name", True), ("version", True), ("version", False)])
def test_stage_resume_finalize_cannot_rebind_same_bytes(full_chain, mutation, cache_loss):
    from universal_video import drive_stage as stage
    job, payload, media, source, staged, drive, result, finalize = full_chain
    before = (source / durable.SOURCE_RECEIPT).read_bytes()
    if cache_loss:
        Path(staged["source"]["path"]).unlink()
    drive.meta[mutation] = "renamed.mov" if mutation == "name" else "2"
    with pytest.raises(stage.DriveStageError, match="binding changed"):
        stage.stage_drive_job(job, payload, media)
    with pytest.raises(RuntimeError):
        finalize()
    assert (source / durable.SOURCE_RECEIPT).read_bytes() == before
    assert not drive.posts and (result / "transcript.txt").exists()


def test_cache_loss_unchanged_source_restores_without_rebinding_and_finalizes(full_chain):
    from universal_video import drive_stage as stage
    job, payload, media, source, staged, drive, result, finalize = full_chain
    before = (source / durable.SOURCE_RECEIPT).read_bytes()
    Path(staged["source"]["path"]).unlink()
    stage.stage_drive_job(job, payload, media)
    assert (source / durable.SOURCE_RECEIPT).read_bytes() == before
    first = finalize()
    assert first == finalize() and len(drive.posts) == 6


def test_missing_pin_retains_existing_cache(full_chain):
    from universal_video import drive_stage as stage
    job, payload, media, source, staged, drive, _, _ = full_chain
    (source / durable.SOURCE_RECEIPT).unlink()
    drive.meta["version"] = "2"
    with pytest.raises(stage.DriveStageError, match="no immutable pin"):
        stage.stage_drive_job(job, payload, media)
    assert Path(staged["source"]["path"]).read_bytes() == CONTENT


@pytest.mark.parametrize("partial", [False, True])
def test_cleanup_failure_restart_preserves_proof_and_never_republishes(full_chain, tmp_path, monkeypatch, partial):
    from universal_video import drive_cleanup as cleanup
    from universal_video import maintenance
    job, _, media, source, _, drive, result, finalize = full_chain
    proof = finalize()
    posts = len(drive.posts)
    spool = tmp_path / "spool"
    (spool / "results").mkdir(parents=True)
    result.rename(spool / "results/job")
    (spool / "done").mkdir()
    done = spool / "done/job.json"
    durable.atomic_json(done, {"job_id": "job", "job_hash": proof["job_hash"], "status": "COMPLETED"})
    pending = cleanup.queue_cleanup(spool, media, "job", done)
    original = cleanup.remove_staged_job
    def interrupted(*args):
        if partial:
            (source / durable.SOURCE_RECEIPT).unlink()
        raise PermissionError("synthetic transient deletion failure")
    monkeypatch.setattr(cleanup, "remove_staged_job", interrupted)
    assert cleanup.retry_cleanup(spool, media, pending) is False
    assert pending.exists() and source.exists()
    assert not any(c.path == spool / "results/job" for c in maintenance.build_cleanup_plan(tmp_path, now=2_000_000_000))
    data = durable.read_receipt(pending)
    data["retry_after_unix"] = 0
    durable.atomic_json(pending, data)
    monkeypatch.setattr(cleanup, "remove_staged_job", original)
    report = maintenance.run_maintenance(tmp_path, dry_run=False)
    assert report["source_cleanups_completed"] == 1
    assert not source.exists() and not pending.exists()
    assert len(drive.posts) == posts and drive.meta["version"] == "1"


@pytest.mark.parametrize("state", ["active", "revalidating"])
def test_stale_maintenance_plan_rechecks_activity_and_current_receipt(full_chain, tmp_path, state):
    from universal_video import maintenance
    _, payload, _, _, _, _, result, finalize = full_chain
    finalize()
    spool = tmp_path / "spool"
    (spool / "results").mkdir(parents=True)
    target = spool / "results/job"
    result.rename(target)
    old = 1_000_000_000
    os.utime(target, (old, old))
    plan = maintenance.build_cleanup_plan(tmp_path)
    assert target in {c.path for c in plan}
    if state == "active":
        (spool / "inbox").mkdir()
        durable.atomic_json(spool / "inbox/job.json", payload)
    else:
        durable.atomic_json(target / durable.FINAL_RECEIPT, {"status": "REVALIDATING"})
        os.utime(target, (old, old))  # defeat reliance on directory mtime alone
    report = maintenance.apply_cleanup_plan(tmp_path, plan, dry_run=False)
    assert target.exists() and report["deleted"] == 0


@pytest.mark.parametrize("block", ["active", "changed_proof", "exhausted"])
def test_pending_cleanup_blocks_active_changed_or_exhausted_job(full_chain, tmp_path, block):
    from universal_video import drive_cleanup as cleanup
    _, payload, media, source, _, drive, result, finalize = full_chain
    proof = finalize()
    posts = len(drive.posts)
    spool = tmp_path / "spool"
    (spool / "results").mkdir(parents=True)
    result.rename(spool / "results/job")
    (spool / "done").mkdir()
    done = spool / "done/job.json"
    durable.atomic_json(done, {"job_id": "job", "job_hash": proof["job_hash"], "status": "COMPLETED"})
    pending = cleanup.queue_cleanup(spool, media, "job", done)
    if block == "active":
        (spool / "inbox").mkdir()
        durable.atomic_json(spool / "inbox/job.json", payload)
        assert cleanup.retry_cleanup(spool, media, pending) is False
    elif block == "exhausted":
        data = durable.read_receipt(pending)
        data["attempts"] = 3
        durable.atomic_json(pending, data)
        assert cleanup.retry_cleanup(spool, media, pending) is False
    else:
        durable.atomic_json(spool / "results/job" / durable.FINAL_RECEIPT, {"status": "REVALIDATING"})
        with pytest.raises((RuntimeError, KeyError)):
            cleanup.retry_cleanup(spool, media, pending)
    assert source.exists() and pending.exists() and len(drive.posts) == posts


@pytest.mark.skipif(os.name != "posix", reason="Linux resident directory fsync/fcntl contract")
@pytest.mark.parametrize("preserve_completion", [True, False], ids=["fixed-recovery", "legacy-rewrite-fault"])
def test_full_intent_crash_orphan_reuse_cleanup_with_real_conformance(tmp_path, monkeypatch, preserve_completion):
    from universal_video import spool_worker as worker, drive_stage as stage, drive_cleanup as cleanup
    from universal_video.contract import validate_job, canonical_job_hash
    from test_universal_video_result_conformance import _bundle, _fingerprint

    monkeypatch.setitem(globals(), "CONTENT", b"v" * (1024 * 1024))
    monkeypatch.setitem(globals(), "SHA", hashlib.sha256(CONTENT).hexdigest())
    backend = FakeDrive()
    backend.meta["size"] = str(len(CONTENT))
    provider_md5 = hashlib.md5(CONTENT, usedforsecurity=False).hexdigest()
    backend.meta["md5Checksum"] = provider_md5
    original_metadata = dict(backend.meta)
    monkeypatch.setattr(adapter.requests, "get", backend.get)
    monkeypatch.setattr(adapter.requests, "post", backend.post)
    monkeypatch.setattr(stage, "access_token", lambda: "synthetic-token")
    monkeypatch.setattr(durable, "access_token", lambda: "synthetic-token")
    spool = tmp_path / "spool"
    (spool / "inbox").mkdir(parents=True)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(media))
    payload = {"job_id": "exact-video-job", "profile": "bridge_lesson",
               "source": {"kind": "google_drive", "file_id": SOURCE}}
    (spool / "inbox/job.json").write_text(json.dumps(payload))
    job_hash = canonical_job_hash(validate_job(payload))
    monkeypatch.setattr(worker, "validate_video_runtime", lambda: None)
    monkeypatch.setattr(worker, "validate_staged_video", lambda _: None)
    binding = {"source_file_id": SOURCE, "folders": dict(FOLDERS)}
    monkeypatch.setattr(worker, "configured_binding", lambda *args: binding)
    computes = []
    def synthetic_producer(staged, output):
        computes.append("fixture_generation")
        result, manifest = _bundle(output)
        source_fp = _fingerprint({"kind": "google_drive", "file_id": SOURCE,
                                 "size_bytes": len(CONTENT), "checksum_kind": "md5Checksum", "checksum": provider_md5})
        manifest.update(job_hash=job_hash, source_fingerprint=source_fp,
                        media={"sha256": SHA, "size_bytes": len(CONTENT), "duration_seconds": 90.0})
        manifest["source"].update(file_id=SOURCE, size=str(len(CONTENT)), md5Checksum=provider_md5,
                                  fingerprint=source_fp)
        durable.atomic_json(result / "manifest.json", manifest)
        return manifest
    monkeypatch.setattr(worker, "run_job", synthetic_producer)
    original_queue = worker.queue_cleanup
    def crash_after_intent(*args):
        pending = original_queue(*args)
        assert pending.exists() and (spool / "running/job.json").exists()
        raise KeyboardInterrupt("synthetic power loss after durable cleanup intent")
    monkeypatch.setattr(worker, "queue_cleanup", crash_after_intent)
    with pytest.raises(KeyboardInterrupt):
        worker.process_one(spool)
    source = media / "drive-ready/exact-video-job"
    result = spool / "results/exact-video-job"
    done = spool / "done/job.json"
    original_done = done.read_bytes()
    original_final = (result / durable.FINAL_RECEIPT).read_bytes()
    original_pin = (source / durable.SOURCE_RECEIPT).read_bytes()
    first = durable.read_receipt(done)
    assert first["result_conformance"]["state"] == "PASS"
    assert first["result_conformance"]["evidence_phase"] == "GENERATION_FINALIZATION"
    posts = len(backend.posts)
    assert durable.cleanup_proof_matches(result, source, job_id="exact-video-job")
    assert worker.recover_orphaned_jobs(spool)["recovered"] == 1
    retry = spool / "progress/exact-video-job.recovery.json"
    state = durable.read_receipt(retry)
    state["retry_after_unix"] = 0
    durable.atomic_json(retry, state)
    monkeypatch.setattr(worker, "queue_cleanup", original_queue)
    if not preserve_completion:
        # Reproduce the previous done overwrite exactly, without replacing the
        # real conformance, finalizer, receipts or cleanup hash gates.
        monkeypatch.setattr(worker, "preserve_pending_completion", lambda *args: False)
    assert worker.process_one(spool)
    assert computes == ["fixture_generation"] and len(backend.posts) == posts
    if not preserve_completion:
        from universal_video.maintenance import run_maintenance
        assert done.read_bytes() != original_done
        assert durable.read_receipt(done)["result_conformance"]["evidence_phase"] == "REUSE_OBSERVATION"
        pending = spool / "cleanup_pending/exact-video-job.json"
        assert durable.read_receipt(pending)["done_sha256"] != hashlib.sha256(done.read_bytes()).hexdigest()
        assert source.exists() and (spool / "failed/job.json").exists()
        assert run_maintenance(tmp_path, dry_run=False)["source_cleanups_completed"] == 0
        assert source.exists() and backend.meta == original_metadata
        return
    assert done.read_bytes() == original_done
    assert (result / durable.FINAL_RECEIPT).read_bytes() == original_final
    assert not source.exists() and not list((spool / "cleanup_pending").glob("*.json"))
    assert backend.meta == original_metadata and SOURCE not in backend.files
    assert not (spool / "failed/job.json").exists()
    assert original_pin  # real pin was retained through restart, then authorized cleanup


def test_retry_reads_back_all_outputs_without_duplicate_posts(setup):
    result, source, drive, _ = setup
    first = finish(setup)
    posts = len(drive.posts)
    second = finish(setup)
    assert first == second
    assert len(drive.posts) == posts == 6  # five artifacts + last durable marker
    assert durable.cleanup_proof_matches(result, source, job_id="job")
    assert all(meta["parents"][0] in FOLDERS.values() for _, meta in drive.posts)
    assert SOURCE not in drive.files and drive.meta == META


@pytest.mark.parametrize("failure", ["fail_upload", "fail_readback", "corrupt", "reparent"])
def test_failure_never_commits_cleanup_proof_and_retry_recovers(setup, failure):
    result, source, drive, _ = setup
    setattr(drive, failure, True)
    with pytest.raises((RuntimeError, requests.ConnectionError)):
        finish(setup)
    assert not (result / durable.FINAL_RECEIPT).exists()
    assert not durable.cleanup_proof_matches(result, source, job_id="job")
    assert source.exists() and (result / "transcript.txt").exists()
    setattr(drive, failure, False)
    finish(setup)
    assert durable.cleanup_proof_matches(result, source, job_id="job")


@pytest.mark.parametrize("field,value", [("name", "renamed.mp4"), ("parents", ["moved"]), ("version", "2"), ("trashed", True)])
def test_original_mutation_blocks_before_any_upload(setup, field, value):
    result, source, drive, _ = setup
    drive.meta[field] = value
    with pytest.raises(RuntimeError):
        finish(setup)
    assert not drive.posts
    assert not durable.cleanup_proof_matches(result, source, job_id="job")


@pytest.mark.parametrize("after_commit", [False, True])
def test_crash_at_local_receipt_boundary_is_idempotently_recovered(setup, monkeypatch, after_commit):
    result, source, drive, _ = setup
    original = durable.atomic_json
    def crash(path, data):
        if path.name == durable.FINAL_RECEIPT:
            if after_commit:
                original(path, data)
            raise KeyboardInterrupt("synthetic power loss")
        return original(path, data)
    monkeypatch.setattr(durable, "atomic_json", crash)
    with pytest.raises(KeyboardInterrupt):
        finish(setup)
    assert source.exists()
    posts = len(drive.posts)
    monkeypatch.setattr(durable, "atomic_json", original)
    finish(setup)
    assert len(drive.posts) == posts
    assert durable.cleanup_proof_matches(result, source, job_id="job")


def test_changed_local_result_after_receipt_blocks_cleanup_and_retry(setup):
    result, source, _, _ = setup
    finish(setup)
    (result / "transcript.txt").write_text("changed")
    assert not durable.cleanup_proof_matches(result, source, job_id="job")
    with pytest.raises(RuntimeError, match="conflicts"):
        finish(setup)


def test_changed_remote_output_is_not_trusted_on_retry(setup):
    result, source, drive, _ = setup
    finish(setup)
    drive.corrupt = True
    with pytest.raises(RuntimeError, match="read-back"):
        finish(setup)
    assert source.exists()


def test_registry_has_no_implicit_or_queue_provided_grant(tmp_path, monkeypatch):
    monkeypatch.delenv("UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE", raising=False)
    assert durable.configured_binding("job", SOURCE, "a" * 64) is None
    registry = tmp_path / "approved.json"
    entry = {"job_id": "job", "source_file_id": SOURCE, "job_hash": "a" * 64,
             "processing_upload_authorized": True, "folders": dict(FOLDERS)}
    registry.write_text(json.dumps({"schema": "universal-video-drive-bindings-v1", "jobs": [entry]}))
    registry.chmod(0o444)
    monkeypatch.setenv("UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE", str(registry))
    assert durable.configured_binding("job", SOURCE, "a" * 64) == entry
    with pytest.raises(RuntimeError, match="authorization mismatch"):
        durable.configured_binding("job", SOURCE, "b" * 64)


@pytest.mark.parametrize("mib", [400, 600, 2048])
def test_large_remote_source_hash_uses_fixed_chunks_no_full_buffer(monkeypatch, mib):
    chunk = b"v" * (8 * 1024 * 1024)
    count = mib // 8
    digest = hashlib.sha256()
    for _ in range(count):
        digest.update(chunk)
    class Large(Response):
        def iter_content(self, chunk_size):
            assert chunk_size == len(chunk)
            for _ in range(count):
                yield chunk
    def get(url, **kw):
        assert kw["stream"] is True
        return Large()
    monkeypatch.setattr(adapter.requests, "get", get)
    proof = adapter.hash_remote_file(SOURCE, "synthetic", expected_size=mib * 1024 * 1024,
                                      expected_sha256=digest.hexdigest())
    assert proof["size_bytes"] == mib * 1024 * 1024


def test_interrupted_download_restarts_without_published_partial(tmp_path, monkeypatch):
    class Interrupted(Response):
        def iter_content(self, chunk_size):
            yield b"partial"
            raise KeyboardInterrupt("synthetic interrupt")
    monkeypatch.setattr(adapter.requests, "get", lambda *a, **kw: Interrupted())
    destination = tmp_path / ".source.part"
    with pytest.raises(KeyboardInterrupt):
        adapter.download_file(SOURCE, destination, "synthetic", metadata=META)
    assert not destination.exists()


@pytest.mark.parametrize("after_rename", [False, True])
def test_partial_quarantine_transition_recovers_both_crash_boundaries(tmp_path, monkeypatch, after_rename):
    partial = tmp_path / "job"
    partial.mkdir()
    (partial / "only-transcript.txt").write_text("preserve")
    recovery = tmp_path / "recovery/job"
    receipt = tmp_path / "compute.json"
    original = Path.rename
    def crash(path, target):
        if path == partial:
            if after_rename:
                original(path, target)
            raise KeyboardInterrupt("synthetic rename power loss")
        return original(path, target)
    monkeypatch.setattr(Path, "rename", crash)
    with pytest.raises(KeyboardInterrupt):
        durable.prepare_compute_recovery(partial, recovery, receipt, job_hash="a" * 64)
    assert durable.read_receipt(receipt)["state"] == "PREPARED"
    monkeypatch.setattr(Path, "rename", original)
    durable.prepare_compute_recovery(partial, recovery, receipt, job_hash="a" * 64)
    state = durable.read_receipt(receipt)
    assert state["state"] == "STARTED" and state["attempts"] == 1
    assert (recovery / state["quarantine_name"] / "only-transcript.txt").read_text() == "preserve"
    with pytest.raises(RuntimeError, match="outcome unknown"):
        durable.prepare_compute_recovery(partial, recovery, receipt, job_hash="a" * 64)
