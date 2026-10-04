"""Synthetic Drive protocol tests. No credentials, real media or live receipts."""
import hashlib
import json
import re
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
                        {"original": adapter.original_snapshot(META), "sha256": SHA})
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
