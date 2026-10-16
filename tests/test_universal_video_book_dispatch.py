"""Synthetic refusal/recovery tests for new review implementation v4.

No real IDs, source-book text, credentials, network requests or subprocesses.
The sandbox integration itself still requires qualified Linux verification.
"""
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest

from bridge_contracts.book_material import digest, identity, produce_bundle
from universal_video.book_contract import (
    BookJobError, INPUT_NAMES, strict_json, validate_book_job,
)
from universal_video import book_runner, book_worker
from universal_video.durable_drive import atomic_json, configured_binding


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def synthetic_inputs():
    pdf = b"%PDF-SYNTHETIC-NOT-A-REAL-BOOK\n"
    source_id = "synthetic_source_01"
    source = {
        "source_id": "SYNTHETIC", "edition_id": "SYNTHETIC-EDITION",
        "title": "Synthetic fixture", "page_count": 3,
        "locator": "https://drive.google.com/file/d/" + source_id + "/view",
        "rendition_sha256": hashlib.sha256(pdf).hexdigest(),
    }
    record = {
        "kind": "ATOM", "origin": "INFERENCE", "rights": "INTERNAL_ALLOWED", "errata_ids": [],
        "anchor": {
            "pdf_page_index": 1, "printed_page_label": None, "bbox": [0, 0, 10, 10],
            "bbox_units": "PDF_POINTS_TOP_LEFT", "segment_hash_method": "RENDER_BYTES_SHA256",
            "segment_sha256": "b" * 64,
        },
        "content": {
            "domain": "CARD_PLAY", "statement": "Synthetic bounded proposition",
            "conditions": ["Synthetic condition"], "exceptions": ["Synthetic exception"],
        },
    }
    bundle = produce_bundle(
        source, {"run_id": "SYNTHETIC", "model_version": "synthetic", "prompt_version": "synthetic"},
        [record], legacy_ids={identity(source, record): "FACT-SYNTHETIC"},
    )
    atom = bundle["objects"][0]
    draft = {
        "status": "DRAFT_NOT_A_TRUSTED_PUBLICATION_RECEIPT",
        "publication_performed": False, "numeric_confidence": None,
        "runtime_receipt_candidate": {"claims": [{
            "object_id": atom["object_id"], "content_sha256": digest(atom["content"]),
            "anchor_sha256": digest(atom["anchor"]),
            "checks": {"semantic_dedup": "PENDING_PRODUCTION_READBACK"},
        }]},
    }
    data = {
        "source_unit": encoded({"synthetic_unit": True}),
        "independent_review": encoded({"synthetic_review": True}),
        "expected_candidate": encoded(bundle), "expected_draft_binding": encoded(draft),
        "producer": b"raise SystemExit('synthetic input must never execute')\n",
    }
    payload = {
        "schema": "book-single-atom-job-v1", "enabled": False,
        "kind": "book_single_atom", "mode": "reproduce_and_verify",
        "job_id": "synthetic-book", "code_commit": "c" * 40,
        "source": {"drive_file_id": source_id, "mime_type": "application/pdf",
                   "bytes": len(pdf), "pages": 3, "sha256": hashlib.sha256(pdf).hexdigest()},
        "scope": {"pdf_page_indexes": [0, 1, 2], "max_atoms": 1,
                  "object_id": "FACT-SYNTHETIC", "preserve_legacy_id": True,
                  "contour": "WORLD_EXTERNAL", "domain": "CARD_PLAY",
                  "publication": "NOT_PUBLISHED"},
        "inputs": [
            {"role": role, "name": name, "drive_file_id": "synthetic_input_%02d" % index,
             "bytes": len(data[role]), "sha256": hashlib.sha256(data[role]).hexdigest()}
            for index, (role, name) in enumerate(INPUT_NAMES.items())
        ],
        "limits": {"concurrency": 1, "cpu": 1, "memory_mib": 2048, "workspace_mib": 512,
                   "wall_seconds": 900, "max_output_files": 32,
                   "max_output_file_bytes": 5242880, "max_output_total_bytes": 16777216,
                   "llm_calls": 0, "dds_calls": 0},
        "permissions": {key: False for key in (
            "database_writes", "canon_changes", "public_uploads",
            "original_changes", "install_dependencies",
        )},
    }
    by_id = {source_id: pdf}
    by_id.update({item["drive_file_id"]: data[item["role"]] for item in payload["inputs"]})
    return payload, data, by_id



def png_chunk(kind, payload):
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))


def valid_png(*, scanline=b"\x00\x00\x00\x00", compressed=None, end=True):
    # Complete synthetic 1x1 RGB PNG; no copyrighted page material.
    return (b"\x89PNG\r\n\x1a\n"
            + png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + png_chunk(b"IDAT", zlib.compress(scanline) if compressed is None else compressed)
            + (png_chunk(b"IEND", b"") if end else b""))

def result_fixture(root):
    payload, data, _ = synthetic_inputs()
    job = validate_book_job(payload)
    root.mkdir()
    for role in ("expected_candidate", "expected_draft_binding"):
        (root / INPUT_NAMES[role]).write_bytes(data[role])
    (root / "renders").mkdir()
    rows = []
    for index in range(3):
        raw = valid_png()
        relative = "renders/p%d.png" % (index + 1)
        (root / relative).write_bytes(raw)
        rows.append({"pdf_page_index": index, "path": relative,
                     "sha256": hashlib.sha256(raw).hexdigest(), "dimensions_px": [1, 1]})
    binaries = {name: "d" * 64 for name in ("bwrap", "python3", "pdfinfo", "pdftoppm")}
    atomic_json(root / "render_manifest.json", {
        "schema": "book-render-v1", "dpi": 150, "renderer_sha256": binaries["pdftoppm"],
        "pages": rows, "legacy_render_hash_equivalence": "NOT_ESTABLISHED",
    })
    atomic_json(root / "review_report.json", book_runner._review(job))
    atomic_json(root / "manifest.json", {
        "schema": "book-reproduction-result-v1", "status": "COMPLETED",
        "job_id": job.job_id, "job_hash": job.job_hash, "profile": job.profile,
        "book_job": payload, "processing_revision": payload["code_commit"],
        "runtime_binary_sha256": binaries, "publication_state": "NOT_PUBLISHED",
    })
    return job


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(extra=True),
    lambda p: p.update(enabled=True),
    lambda p: p.update(job_id="../escape"),
    lambda p: p["source"].update(mime_type="video/mp4"),
    lambda p: p["source"].update(bytes=True),
    lambda p: p["scope"].update(pdf_page_indexes=[0, 0, 1]),
    lambda p: p["scope"].update(max_atoms=True),
    lambda p: p["scope"].update(domain="BIDDING"),
    lambda p: p["limits"].update(wall_seconds=901),
    lambda p: p["limits"].update(llm_calls=1),
    lambda p: p["limits"].update(workspace_mib=1),
    lambda p: p["permissions"].update(database_writes=True),
    lambda p: p["inputs"][0].update(name="../executable.py"),
    lambda p: p["inputs"][0].update(drive_file_id=p["source"]["drive_file_id"]),
])
def test_inbox_cannot_widen_scope_or_permissions(mutate):
    payload, _, _ = synthetic_inputs()
    mutate(payload)
    with pytest.raises(BookJobError):
        validate_book_job(payload)


def test_duplicate_json_and_nonfinite_numbers_rejected():
    for raw in ('{"enabled":false,"enabled":true}', '{"value":NaN}', '{"value":Infinity}'):
        with pytest.raises(BookJobError):
            strict_json(raw)


def test_default_disabled_before_auth_download_or_binding(monkeypatch):
    monkeypatch.delenv("UNIVERSAL_VIDEO_BOOK_DISPATCH", raising=False)
    binding = Mock()
    monkeypatch.setattr(book_worker, "configured_binding", binding)
    with pytest.raises(BookJobError, match="disabled"):
        book_worker.process_claimed_book(
            synthetic_inputs()[0], None, "synthetic.json", {}, None, None, progress=None,
        )
    binding.assert_not_called()


@pytest.mark.parametrize("change", [
    {"trusted_publication_receipt": True}, {"semantic_dedup": "PASS"},
    {"publication": "PUBLISHED"}, {"legacy_render_hash_equivalence": "PASS"},
])
def test_reproduction_cannot_mint_publication_or_render_authority(tmp_path, change):
    result = tmp_path / "result"
    job = result_fixture(result)
    technical = book_runner.verify_book_result(
        result, expected_job_id=job.job_id, expected_job_hash=job.job_hash,
    )
    assert technical["state"] == "PASS" and technical["bridge_production_ready"] is False
    report = book_runner.json_file(result / "review_report.json")
    report.update(change)
    atomic_json(result / "review_report.json", report)
    with pytest.raises(BookJobError, match="authority"):
        book_runner.verify_book_result(
            result, expected_job_id=job.job_id, expected_job_hash=job.job_hash,
        )


def test_changed_candidate_and_render_bytes_are_not_reusable(tmp_path):
    result = tmp_path / "result"
    job = result_fixture(result)
    (result / "renders/p1.png").write_bytes(b"not the pinned render")
    with pytest.raises(BookJobError, match="render bytes"):
        book_runner.verify_book_result(
            result, expected_job_id=job.job_id, expected_job_hash=job.job_hash,
        )
    _, data, _ = synthetic_inputs()
    (result / INPUT_NAMES["expected_candidate"]).write_bytes(data["expected_candidate"] + b" ")
    with pytest.raises(BookJobError, match="regular|bytes"):
        book_runner.verify_candidate(job, result)


def test_original_and_unlisted_files_never_enter_upload_set(tmp_path):
    result = tmp_path / "result"
    result_fixture(result)
    (result / "source.pdf").write_bytes(b"synthetic original")
    with pytest.raises(BookJobError, match="allowlisted"):
        book_runner.collect_book_artifacts(result)


def test_symlink_cannot_smuggle_an_external_file(tmp_path):
    result = tmp_path / "result"
    result_fixture(result)
    outside = tmp_path / "outside"
    outside.write_bytes(b"synthetic outside")
    (result / "renders/p1.png").unlink()
    (result / "renders/p1.png").symlink_to(outside)
    with pytest.raises(BookJobError, match="symlink"):
        book_runner.collect_book_artifacts(result)


def test_unknown_compute_outcome_is_never_implicitly_repeated(tmp_path, monkeypatch):
    job = validate_book_job(synthetic_inputs()[0])
    (tmp_path / job.job_id).mkdir()
    sandbox = Mock()
    monkeypatch.setattr(book_runner, "qualified_runtime", lambda *args: {})
    monkeypatch.setattr(book_runner, "_sandbox", sandbox)
    with pytest.raises(BookJobError, match="prior computation"):
        book_runner.reproduce_book_job(job, tmp_path, tmp_path, {}, tmp_path, time.monotonic() + 1)
    sandbox.assert_not_called()


def stage_mocks(monkeypatch, payload, by_id):
    specs = {item["drive_file_id"]: item for item in payload["inputs"]}
    specs[payload["source"]["drive_file_id"]] = {
        **payload["source"], "name": "source.pdf", "role": "original",
    }

    def metadata(file_id, token):
        spec = specs[file_id]
        return {
            "id": file_id, "name": spec["name"], "size": str(len(by_id[file_id])),
            "mimeType": ("application/pdf" if spec["role"] == "original" else
                         "text/x-python" if spec["role"] == "producer" else "application/json"),
            "version": "1", "modifiedTime": "synthetic-time",
            "parents": ["synthetic_folder_01"], "trashed": False,
        }

    def download(file_id, path, token, **kwargs):
        path.write_bytes(by_id[file_id])

    def remote_hash(file_id, token, *, expected_size, expected_sha256):
        raw = by_id[file_id]
        assert len(raw) == expected_size
        assert hashlib.sha256(raw).hexdigest() == expected_sha256
        return {"sha256": hashlib.sha256(raw).hexdigest()}

    from universal_video import durable_drive
    monkeypatch.setattr(book_runner, "access_token", lambda: "synthetic-token")
    monkeypatch.setattr(book_runner, "file_metadata", metadata)
    monkeypatch.setattr(book_runner, "download_file", download)
    monkeypatch.setattr(book_runner.shutil, "disk_usage", lambda path: SimpleNamespace(free=1 << 30))
    monkeypatch.setattr(durable_drive, "file_metadata", metadata)
    monkeypatch.setattr(durable_drive, "hash_remote_file", remote_hash)
    return metadata


def test_same_size_download_corruption_never_becomes_ready(tmp_path, monkeypatch):
    payload, _, by_id = synthetic_inputs()
    job = validate_book_job(payload)
    stage_mocks(monkeypatch, payload, by_id)
    original = payload["source"]["drive_file_id"]
    by_id[original] = b"!" + by_id[original][1:]
    with pytest.raises(BookJobError, match="digest"):
        book_runner.stage_book_job(job, tmp_path)
    staged = tmp_path / "drive-ready" / job.job_id
    assert not (staged / "source.pdf").exists()
    assert (staged / "SOURCE_INTEGRITY.json").exists()


def test_changed_remote_version_cannot_rebind_cached_bytes(tmp_path, monkeypatch):
    payload, _, by_id = synthetic_inputs()
    job = validate_book_job(payload)
    metadata = stage_mocks(monkeypatch, payload, by_id)
    staged = book_runner.stage_book_job(job, tmp_path)
    pin_before = (staged / "SOURCE_INTEGRITY.json").read_bytes()

    def changed(file_id, token):
        return {**metadata(file_id, token), "version": "2"}

    monkeypatch.setattr(book_runner, "file_metadata", changed)
    with pytest.raises(BookJobError, match="pin changed"):
        book_runner.stage_book_job(job, tmp_path)
    assert (staged / "SOURCE_INTEGRITY.json").read_bytes() == pin_before


def test_non_pdf_mime_fails_before_download(tmp_path, monkeypatch):
    payload, _, by_id = synthetic_inputs()
    metadata = stage_mocks(monkeypatch, payload, by_id)
    monkeypatch.setattr(book_runner, "file_metadata",
                        lambda file_id, token: {**metadata(file_id, token), "mimeType": "video/mp4"})
    download = Mock()
    monkeypatch.setattr(book_runner, "download_file", download)
    with pytest.raises(BookJobError, match="metadata"):
        book_runner.stage_book_job(validate_book_job(payload), tmp_path)
    download.assert_not_called()


def test_old_video_binding_registry_never_authorizes_book(tmp_path, monkeypatch):
    registry = tmp_path / "bindings.json"
    atomic_json(registry, {"schema": "universal-video-drive-bindings-v1", "jobs": []})
    registry.chmod(0o600)
    monkeypatch.setenv("UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE", str(registry))
    with pytest.raises((RuntimeError, BookJobError, OSError), match="registry|protected|Permission|No such"):
        configured_binding("synthetic-book", "synthetic_source_01", "a" * 64, kind="book")

@pytest.mark.parametrize("corruption", ["header_only", "crc", "deflate", "missing_iend",
                                        "short_image", "bad_filter", "trailing", "extra_image"])
def test_complete_png_validation_rejects_all_truncation_and_decode_failures(tmp_path, corruption):
    raw = valid_png()
    if corruption == "header_only": raw = raw[:24]
    if corruption == "crc": raw = raw[:-1] + bytes([raw[-1] ^ 1])
    if corruption == "deflate": raw = valid_png(compressed=b"not-zlib")
    if corruption == "missing_iend": raw = valid_png(end=False)
    if corruption == "short_image": raw = valid_png(scanline=b"\x00")
    if corruption == "bad_filter": raw = valid_png(scanline=b"\x05\x00\x00\x00")
    if corruption == "trailing": raw += b"extra"
    if corruption == "extra_image": raw = valid_png(scanline=b"\x00" * 8)
    path = tmp_path / "synthetic.png"
    path.write_bytes(raw)
    with pytest.raises(BookJobError):
        book_runner.png_size(path, 1024)


def test_complete_synthetic_png_is_decodable(tmp_path):
    path = tmp_path / "synthetic.png"
    path.write_bytes(valid_png())
    assert book_runner.png_size(path, 1024) == [1, 1]


def test_sandbox_never_exposes_expected_files(tmp_path, monkeypatch):
    job = validate_book_job(synthetic_inputs()[0])
    staged, output, code = tmp_path / "private-input", tmp_path / "out", tmp_path / "code"
    for path in (staged, output, code):
        path.mkdir()
    observed = []
    def popen(args, **kwargs):
        observed.extend(args)
        return SimpleNamespace(wait=lambda **kwargs: 0)
    monkeypatch.setattr(book_runner.subprocess, "Popen", popen)
    binary = {name: "/usr/bin/" + name for name in ("bwrap", "python3", "pdfinfo", "pdftoppm")}
    book_runner._sandbox(job, staged, output, code, binary, ["/usr/bin/true"],
                         time.monotonic() + 30, staged / "log")
    assert str(staged) not in observed
    assert str(staged / "source.pdf") in observed
    for role in ("expected_candidate", "expected_draft_binding"):
        assert not any(INPUT_NAMES[role] in argument for argument in observed)
    assert "--size" in observed and "--cap-drop" in observed and "--remount-ro" in observed


def worker_paths(root):
    root.mkdir()
    paths = {}
    for name in ("inbox", "running", "done", "failed", "results", "progress"):
        paths[name] = root / name
        paths[name].mkdir()
    return paths


def worker_mocks(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_BOOK_DISPATCH", "qualified")
    monkeypatch.setattr(book_worker, "private_directory", lambda *a, **k: None)
    monkeypatch.setattr(book_worker, "resource_boundaries", lambda *a: {})
    monkeypatch.setattr(book_worker, "cleanup_proof_matches", lambda *a, **k: False)
    monkeypatch.setattr(book_worker, "os", SimpleNamespace(
        **{name: getattr(os, name) for name in (
            "getenv", "open", "fsync", "close", "rename", "O_RDONLY", "O_DIRECTORY", "O_NOFOLLOW")},
        geteuid=lambda: 999999, name="posix"))
    real_lstat = type(Path("/")).lstat
    def lstat(path):
        info = real_lstat(path)
        if path.name == "inbox":
            return SimpleNamespace(st_mode=info.st_mode, st_uid=1111)
        return info
    monkeypatch.setattr(type(Path("/")), "lstat", lstat)
    monkeypatch.setattr(book_worker, "_binding", lambda job: {
        "inbox_uid": 1111,
        "runtime_binary_sha256": {name: "d" * 64 for name in ("bwrap", "python3", "pdfinfo", "pdftoppm")},
    })
    from contextlib import contextmanager
    @contextmanager
    def deadline(seconds):
        yield time.monotonic() + seconds
    monkeypatch.setattr(book_worker, "deadline_guard", deadline)


def proof_fixture(job):
    return {"job_hash": job.job_hash, "job_id": job.job_id,
            "status": "PUBLISHED_VERIFIED", "remote_verification": "CONTENT_READBACK_SHA256",
            "canonical_promotion_allowed": False,
            "remote_receipt": {"verification": "CONTENT_READBACK_SHA256"}}


def completion_fixture(job, root):
    return {"job_hash": job.job_hash, "job_id": job.job_id,
            "compute_status": "COMPLETED", "publication_state": "NOT_PUBLISHED",
            "drive_finalization": proof_fixture(job), "result_dir": str(root)}


@pytest.mark.parametrize("phase", ["REMOTE_VERIFIED", "COMPLETED"])
def test_durable_completion_precedes_staging_when_done_and_results_removed(tmp_path, monkeypatch, phase):
    import os
    worker_mocks(monkeypatch)
    job = validate_book_job(synthetic_inputs()[0])
    paths = worker_paths(tmp_path / "spool")
    ledger_path, ledger = book_worker._ledger(paths, job)
    atomic_json(ledger_path, {**ledger, "phase": phase, "compute_status": "COMPLETED",
                             "completion": completion_fixture(job, paths["results"] / job.job_id)})
    claimed = paths["running"] / (job.job_id + ".json")
    claimed.write_bytes(encoded(job.payload))
    forbidden = Mock(side_effect=AssertionError("repeat compute/cloud operation forbidden"))
    for name in ("stage_book_job", "reproduce_book_job", "finalize_drive_job", "_binding"):
        monkeypatch.setattr(book_worker, name, forbidden)
    result = book_worker.process_claimed_book(
        job.payload, claimed, claimed.name, paths, tmp_path / "spool", tmp_path, progress=Mock())
    assert result["status"] == "COMPLETED" and not claimed.exists()
    assert book_worker.json_file(paths["done"] / claimed.name) == completion_fixture(
        job, paths["results"] / job.job_id)
    forbidden.assert_not_called()


@pytest.mark.parametrize("state", ["STARTED", "COMPLETED", "EXHAUSTED"])
def test_missing_retained_compute_or_exhausted_budget_stops_before_staging(tmp_path, monkeypatch, state):
    worker_mocks(monkeypatch)
    job = validate_book_job(synthetic_inputs()[0])
    paths = worker_paths(tmp_path / "spool")
    ledger_path, ledger = book_worker._ledger(paths, job)
    atomic_json(ledger_path, {**ledger, "compute_status": "COMPLETED" if state == "EXHAUSTED" else state,
                             "finalization_attempts": 3 if state == "EXHAUSTED" else 1})
    claimed = paths["running"] / (job.job_id + ".json")
    claimed.write_bytes(encoded(job.payload))
    stage, compute = Mock(), Mock()
    monkeypatch.setattr(book_worker, "stage_book_job", stage)
    monkeypatch.setattr(book_worker, "reproduce_book_job", compute)
    result = book_worker.process_claimed_book(
        job.payload, claimed, claimed.name, paths, tmp_path / "spool", tmp_path, progress=Mock())
    assert result["status"] == "STOP"
    stage.assert_not_called()
    compute.assert_not_called()


@pytest.mark.parametrize("point", [
    *["upload_%d" % i for i in range(8)], *["readback_%d" % i for i in range(8)],
    "marker_upload", "marker_readback", "post_marker_source", "post_marker_acl"])
def test_finalizer_transient_at_every_transfer_point_requeues_without_compute(tmp_path, monkeypatch, point):
    worker_mocks(monkeypatch)
    job = validate_book_job(synthetic_inputs()[0])
    paths = worker_paths(tmp_path / "spool")
    result_fixture(paths["results"] / job.job_id)
    ledger_path, ledger = book_worker._ledger(paths, job)
    atomic_json(ledger_path, {**ledger, "compute_status": "COMPLETED", "phase": "FINALIZING"})
    claimed = paths["running"] / (job.job_id + ".json")
    claimed.write_bytes(encoded(job.payload))
    monkeypatch.setattr(book_worker, "stage_book_job", lambda *a: tmp_path)
    compute = Mock(side_effect=AssertionError("producer must not repeat"))
    monkeypatch.setattr(book_worker, "reproduce_book_job", compute)
    calls = []
    def finalizer(*args, **kwargs):
        calls.append(point)
        raise ConnectionError("synthetic transient at " + point)
    monkeypatch.setattr(book_worker, "finalize_drive_job", finalizer)
    for expected_attempt in (1, 2, 3):
        result = book_worker.process_claimed_book(
            job.payload, claimed, claimed.name, paths, tmp_path / "spool", tmp_path, progress=Mock())
        value = book_worker.json_file(ledger_path)
        assert value["finalization_attempts"] == expected_attempt
        if expected_attempt < 3:
            assert result["status"] == "RETRY_PENDING"
            queued = paths["inbox"] / claimed.name
            assert queued.read_bytes() == encoded(job.payload)
            retry = book_worker.json_file(paths["progress"] / (job.job_id + ".recovery.json"))
            assert retry["job_hash"] == job.job_hash and retry["retry_after_unix"] > time.time()
            queued.rename(claimed)
        else:
            assert result["status"] == "STOP" and value["phase"] == "STOP"
            assert (paths["failed"] / (job.job_id + ".book-stop.payload.json")).exists()
    assert len(calls) == 3
    compute.assert_not_called()



@pytest.mark.parametrize("point", ["done", "intent_before_write", "intent_after_write", "claim_delete"])
def test_real_resident_continues_terminal_completion_and_cleanup_without_restart(tmp_path, monkeypatch, point):
    from universal_video import spool_worker, drive_cleanup
    worker_mocks(monkeypatch)
    job = validate_book_job(synthetic_inputs()[0])
    spool = tmp_path / "spool"
    paths = worker_paths(spool)
    media = tmp_path / "media"
    source = media / "drive-ready" / job.job_id
    source.mkdir(parents=True)
    result = paths["results"] / job.job_id
    result_fixture(result)
    proof = proof_fixture(job)
    atomic_json(result / "DRIVE_FINALIZATION.json", proof)
    pin = {"schema": "universal-video-source-integrity-v1", "job_id": job.job_id,
           "job_hash": job.job_hash, "original": {"id": job.payload["source"]["drive_file_id"]},
           "sha256": job.payload["source"]["sha256"]}
    atomic_json(source / "SOURCE_INTEGRITY.json", pin)
    ledger_path, ledger = book_worker._ledger(paths, job)
    completion = completion_fixture(job, result)
    atomic_json(ledger_path, {**ledger, "phase": "REMOTE_VERIFIED", "compute_status": "COMPLETED",
                             "completion": completion})
    claimed = paths["running"] / (job.job_id + ".json")
    claimed.write_bytes(encoded(job.payload))
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(media))
    monkeypatch.setattr(book_worker, "cleanup_proof_matches", lambda *a, **k: True)
    monkeypatch.setattr(drive_cleanup, "cleanup_proof_matches", lambda *a, **k: True)
    cleanup_calls = []
    # Real queue_cleanup writes its real immutable, hash-bound local intent.
    real_queue = drive_cleanup.queue_cleanup
    def queue(*args):
        if point == "intent_before_write" and not failures:
            failures.append(point)
            raise OSError("synthetic before intent write")
        pending = real_queue(*args)
        if point == "intent_after_write" and not failures:
            failures.append(point)
            raise OSError("synthetic after durable intent write")
        return pending
    monkeypatch.setattr(book_worker, "queue_cleanup", queue)
    def retry(spool, media, pending):
        cleanup_calls.append(pending)
        assert pending.exists() and not claimed.exists()
        return False  # real deletion is separately covered by cleanup module tests
    monkeypatch.setattr(book_worker, "retry_cleanup", retry)
    real_atomic, real_unlink = book_worker.atomic_json, Path.unlink
    failures = []
    def atomic(path, data):
        if point == "done" and path.parent == paths["done"] and not failures:
            failures.append(point)
            raise OSError("synthetic done write failure")
        real_atomic(path, data)
    def unlink(path, *a, **k):
        if point == "claim_delete" and path == claimed and not failures:
            failures.append(point)
            raise OSError("synthetic claim release failure")
        return real_unlink(path, *a, **k)
    monkeypatch.setattr(book_worker, "atomic_json", atomic)
    monkeypatch.setattr(Path, "unlink", unlink)
    forbidden = Mock(side_effect=AssertionError("completion must be local only"))
    for name in ("_binding", "stage_book_job", "reproduce_book_job", "finalize_drive_job"):
        monkeypatch.setattr(book_worker, name, forbidden)
    clock = [1000.0]
    monkeypatch.setattr(book_worker.time, "time", lambda: clock[0])
    # Actual process_one includes the real exclusive lock and resident poll hook.
    assert spool_worker.process_one(spool) is True
    assert failures == [point] and claimed.exists()
    assert not list(paths["failed"].iterdir())
    assert spool_worker.process_one(spool) is False  # shared scheduler backoff
    clock[0] += 61
    assert spool_worker.process_one(spool) is True
    assert not claimed.exists() and (paths["done"] / claimed.name).exists()
    pending = spool / "cleanup_pending" / (job.job_id + ".json")
    assert pending.exists() and cleanup_calls == [pending]
    assert book_worker.json_file(ledger_path)["phase"] == "COMPLETED"
    assert book_worker.json_file(ledger_path)["completion_attempts"] == 2
    assert not list(paths["failed"].iterdir())
    forbidden.assert_not_called()


def test_local_completion_budget_is_bounded_in_actual_resident(tmp_path, monkeypatch):
    from universal_video import spool_worker
    worker_mocks(monkeypatch)
    job = validate_book_job(synthetic_inputs()[0])
    spool = tmp_path / "spool"
    paths = worker_paths(spool)
    ledger_path, ledger = book_worker._ledger(paths, job)
    atomic_json(ledger_path, {**ledger, "phase": "REMOTE_VERIFIED", "compute_status": "COMPLETED",
                             "completion": completion_fixture(job, paths["results"] / job.job_id)})
    claimed = paths["running"] / (job.job_id + ".json")
    claimed.write_bytes(encoded(job.payload))
    clock = [1000.0]
    monkeypatch.setattr(book_worker.time, "time", lambda: clock[0])
    real_atomic = book_worker.atomic_json
    failures = []
    def atomic(path, data):
        if path.parent == paths["done"]:
            failures.append(True)
            raise OSError("persistent synthetic done write failure")
        real_atomic(path, data)
    monkeypatch.setattr(book_worker, "atomic_json", atomic)
    forbidden = Mock(side_effect=AssertionError("no cloud/compute"))
    for name in ("_binding", "stage_book_job", "reproduce_book_job", "finalize_drive_job"):
        monkeypatch.setattr(book_worker, name, forbidden)
    for attempt in range(3):
        assert spool_worker.process_one(spool) is True
        clock[0] += 61
    assert spool_worker.process_one(spool) is False
    assert len(failures) == 3 and claimed.exists()
    assert book_worker.json_file(ledger_path)["phase"] == "REMOTE_VERIFIED"
    assert book_worker.json_file(ledger_path)["completion_attempts"] == 3
    assert not list(paths["failed"].iterdir())
    forbidden.assert_not_called()


def test_root_owned_registry_rejects_writable_parent_and_symlink(tmp_path):
    path = tmp_path / "registry.json"
    path.write_bytes(b"{}")
    with pytest.raises((BookJobError, PermissionError, OSError)):
        book_runner.book_registry(path)
    linked = tmp_path / "alias"
    linked.symlink_to(path)
    with pytest.raises((BookJobError, PermissionError, OSError)):
        book_runner.book_registry(linked)


def test_owner_only_acl_rejects_other_users_and_wrong_owner():
    from universal_video.durable_drive import _book_owner_only
    binding = {"drive_owner_permission_id": "synthetic-owner"}
    owner = {"id": "synthetic-owner", "type": "user", "role": "owner"}
    _book_owner_only({"permissions": [owner]}, binding)
    for permissions in ([], [owner, {"id": "other", "type": "user", "role": "reader"}],
                        [{"id": "wrong", "type": "user", "role": "owner"}],
                        [{"id": "synthetic-owner", "type": "anyone", "role": "owner"}]):
        with pytest.raises(RuntimeError, match="owner-only"):
            _book_owner_only({"permissions": permissions}, binding)


def test_source_commit_environment_cannot_replace_measured_inventory(monkeypatch, tmp_path):
    monkeypatch.setattr(book_runner.sys, "flags", SimpleNamespace(isolated=1, safe_path=True, optimize=0))
    monkeypatch.setattr(book_runner.sys, "dont_write_bytecode", True)
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "c" * 40)
    with pytest.raises(BookJobError, match="source inventory"):
        book_runner.source_boundaries({}, tmp_path)


@pytest.mark.parametrize("kind,capacity", [(0xef53, 128 * 1024 * 1024),
                                          (0x01021994, 600 * 1024 * 1024)])
def test_workspace_needs_actual_finite_filesystem_cap(tmp_path, monkeypatch, kind, capacity):
    monkeypatch.setattr(book_runner, "private_directory", lambda *a, **k: 1)
    monkeypatch.setattr(book_runner, "_filesystem", lambda fd: (kind, capacity))
    job = validate_book_job(synthetic_inputs()[0])
    with pytest.raises(BookJobError, match="bounded tmpfs"):
        book_runner.resource_boundaries(job, tmp_path, tmp_path, tmp_path)

@pytest.mark.parametrize("category,index", [
    *[("upload", i) for i in range(9)],
    *[("readback", i) for i in range(17)],
    ("source", 1), ("source", 2), ("folder_after_marker", 0)])
def test_real_shared_finalizer_failure_points_keep_compute_and_claim(tmp_path, monkeypatch, category, index):
    from universal_video import durable_drive as drive
    worker_mocks(monkeypatch)
    job = validate_book_job(synthetic_inputs()[0])
    paths = worker_paths(tmp_path / "spool")
    result_fixture(paths["results"] / job.job_id)
    staged = tmp_path / "staged"
    staged.mkdir()
    atomic_json(staged / drive.SOURCE_RECEIPT, {
        "schema": "universal-video-source-integrity-v1", "job_id": job.job_id, "job_hash": job.job_hash})
    roles = ("school_root", "processing", "book", "source_job", "analysis", "checks")
    binding = {"inbox_uid": 1111, "source_file_id": job.payload["source"]["drive_file_id"],
               "folders": {role: "synthetic_" + role for role in roles},
               "drive_owner_permission_id": "synthetic-owner",
               "runtime_binary_sha256": {name: "d" * 64 for name in ("bwrap", "python3", "pdfinfo", "pdftoppm")}}
    monkeypatch.setattr(book_worker, "_binding", lambda job: binding)
    monkeypatch.setattr(book_worker, "stage_book_job", lambda *a: staged)
    monkeypatch.setattr(book_worker, "finalize_drive_job", drive.finalize_drive_job)
    compute = Mock(side_effect=AssertionError("no compute repeat"))
    monkeypatch.setattr(book_worker, "reproduce_book_job", compute)
    monkeypatch.setattr(book_worker, "cleanup_proof_matches",
                        lambda *a, **k: (paths["results"] / job.job_id / drive.FINAL_RECEIPT).exists())
    monkeypatch.setattr(drive, "access_token", lambda: "synthetic-token")
    counts = {"upload": 0, "readback": 0, "source": 0, "folder_after_marker": 0}
    failed = []
    uploads = {}
    remote_by_name = {}
    marker_completed = []
    def inject(kind):
        number = counts[kind]
        counts[kind] += 1
        if kind == category and number == index:
            failed.append(kind)
            raise ConnectionError("synthetic actual shared-finalizer boundary")
    def source(*a):
        inject("source")
        return {"file_id": binding["source_file_id"], "before": {"id": binding["source_file_id"]},
                "unchanged": True}
    def upload(folder, artifact, token):
        inject("upload")
        key = (folder, artifact.relative_name)
        identifier = remote_by_name.setdefault(key, "synthetic_remote_%d" % len(remote_by_name))
        uploads[identifier] = artifact
        if artifact.relative_name.endswith("PUBLICATION_COMPLETE.json"):
            marker_completed.append(True)
        return {"file_id": identifier, "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256, "verification": drive.READBACK}
    def metadata(identifier, token):
        return {"id": identifier, "permissions": [
            {"id": "synthetic-owner", "type": "user", "role": "owner"}]}
    def readback(*a):
        inject("readback")
        return {"verification": drive.READBACK}
    def folder(*a, **k):
        if marker_completed:
            inject("folder_after_marker")
        return {}
    monkeypatch.setattr(drive, "verify_source", source)
    monkeypatch.setattr(drive, "_upload_or_verify_file", upload)
    monkeypatch.setattr(drive, "_get_file_metadata", metadata)
    monkeypatch.setattr(drive, "_verify_remote_artifact", readback)
    monkeypatch.setattr(drive, "_verify_folder", folder)
    monkeypatch.setattr(drive, "_book_folder_acl", lambda *a: None)
    ledger_path, ledger = book_worker._ledger(paths, job)
    atomic_json(ledger_path, {**ledger, "phase": "FINALIZING", "compute_status": "COMPLETED"})
    claimed = paths["running"] / (job.job_id + ".json")
    claimed.write_bytes(encoded(job.payload))
    result = book_worker.process_claimed_book(
        job.payload, claimed, claimed.name, paths, tmp_path / "spool", tmp_path, progress=Mock())
    assert failed == [category] and result["status"] == "RETRY_PENDING"
    assert (paths["inbox"] / claimed.name).exists()
    assert (paths["results"] / job.job_id / "manifest.json").exists()
    assert not list(paths["failed"].iterdir())
    assert book_worker.json_file(ledger_path)["compute_status"] == "COMPLETED"
    # Retry the same retained result. Proof remains unavailable until the real
    # finalizer actually writes FINAL_RECEIPT, so no premature terminal shortcut.
    queued = paths["inbox"] / claimed.name
    queued.rename(claimed)
    resumed = book_worker.process_claimed_book(
        job.payload, claimed, claimed.name, paths, tmp_path / "spool", tmp_path, progress=Mock())
    assert resumed["status"] == "COMPLETED" and not claimed.exists()
    assert failed == [category]
    assert len(remote_by_name) == 9  # stable artifact identities, including marker
    assert book_worker.json_file(ledger_path)["phase"] == "COMPLETED"
    compute.assert_not_called()

@pytest.mark.parametrize("change", ["memory", "pids", "cpu", "unlimited", "capability", "swap"])
def test_cgroup_tree_limits_are_measured_not_boolean_attestation(tmp_path, monkeypatch, change):
    job = validate_book_job(synthetic_inputs()[0])
    values = {"memory.max": b"2147483648", "pids.max": b"16",
              "cpu.max": b"100000 100000", "memory.swap.max": b"0"}
    if change == "memory": values["memory.max"] = b"2147483649"
    if change == "pids": values["pids.max"] = b"17"
    if change == "cpu": values["cpu.max"] = b"100001 100000"
    if change == "unlimited": values["memory.max"] = b"max"
    if change == "swap": values["memory.swap.max"] = b"1"
    monkeypatch.setattr(book_runner, "protected_read", lambda path, *a: values[path.name])
    real_read = Path.read_text
    def read_text(path, *a, **k):
        if str(path) == "/proc/self/cgroup": return "0::/synthetic-qualified-worker\n"
        if str(path) == "/proc/self/status":
            return "CapEff:\t" + ("1" if change == "capability" else "0") + "\n"
        return real_read(path, *a, **k)
    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(BookJobError):
        book_runner.cgroup_boundaries(job)


def test_actual_source_bytes_must_match_approved_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(book_runner.sys, "flags", SimpleNamespace(isolated=1, safe_path=True, optimize=0))
    monkeypatch.setattr(book_runner.sys, "dont_write_bytecode", True)
    original = b"synthetic_value = 1\n"
    changed = b"synthetic_value = 2\n"
    binding = {"source_bundle": {"universal_video/synthetic.py": {
        "bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}}}
    binding["source_bundle_sha256"] = digest(binding["source_bundle"])
    monkeypatch.setattr(book_runner, "protected_read", lambda *a: changed)
    with pytest.raises(BookJobError, match="source bytes"):
        book_runner.source_boundaries(binding, tmp_path)

def test_video_comparison_binding_failure_still_blocks_all_output_uploads(tmp_path, monkeypatch):
    from universal_video import durable_drive as drive
    result, staged = tmp_path / "result", tmp_path / "source"
    result.mkdir()
    staged.mkdir()
    atomic_json(staged / drive.SOURCE_RECEIPT, {
        "schema": "universal-video-source-integrity-v1", "job_id": "synthetic-video",
        "job_hash": "a" * 64})
    monkeypatch.setattr(drive, "access_token", lambda: "synthetic-token")
    monkeypatch.setattr(drive, "verify_source", lambda *a: {
        "file_id": "synthetic_original", "before": {"id": "synthetic_original"}})
    atomic_json(result / "manifest.json", {"metadata": {}})
    monkeypatch.setattr(drive, "collect_compact_artifacts", lambda *a: [])
    comparison = Mock(side_effect=RuntimeError("synthetic comparison-original mismatch"))
    upload = Mock()
    monkeypatch.setattr(drive, "verify_comparison_original", comparison)
    monkeypatch.setattr(drive, "_upload_or_verify_file", upload)
    with pytest.raises(RuntimeError, match="comparison-original mismatch"):
        drive.finalize_drive_job(
            result, staged, {"source_file_id": "synthetic_original"},
            job_id="synthetic-video", profile="transcript_only", job_hash="a" * 64)
    comparison.assert_called_once()
    upload.assert_not_called()
    assert (staged / drive.SOURCE_RECEIPT).exists()


def test_video_required_gate_does_not_change_book_finalization(tmp_path, monkeypatch):
    from universal_video import durable_drive as drive
    result = tmp_path / "synthetic-book"
    job = result_fixture(result)
    source_dir = tmp_path / "staged"
    source_dir.mkdir()
    atomic_json(source_dir / drive.SOURCE_RECEIPT, {
        "schema": "universal-video-source-integrity-v1",
        "job_id": job.job_id, "job_hash": job.job_hash, "comparison_required": True})
    roles = ("school_root", "processing", "book", "source_job", "analysis", "checks")
    binding = {"source_file_id": job.payload["source"]["drive_file_id"],
               "folders": {role: "synthetic_" + role for role in roles},
               "drive_owner_permission_id": "synthetic-owner"}
    collect, verify, parents, writable = drive._result_contract("book_single_atom")
    assert collect is book_runner.collect_book_artifacts
    assert verify is book_runner.verify_book_result
    assert writable == {"analysis", "checks"} and parents["book"] == "processing"
    monkeypatch.setattr(drive, "require_comparison_package",
                        lambda *a, **k: pytest.fail("video requirement entered book path"))
    monkeypatch.setattr(drive, "access_token", lambda: "synthetic-token")
    monkeypatch.setattr(drive, "verify_source", lambda *a: {
        "file_id": binding["source_file_id"], "before": {"id": binding["source_file_id"]},
        "unchanged": True})
    remote = {}
    marker = []
    def upload(folder, artifact, token):
        identifier = "synthetic_remote_%d" % len(remote)
        remote[identifier] = artifact
        if artifact.relative_name.endswith("PUBLICATION_COMPLETE.json"):
            marker.append(json.loads(artifact.path.read_bytes()))
        return {"file_id": identifier, "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256, "verification": drive.READBACK}
    monkeypatch.setattr(drive, "_upload_or_verify_file", upload)
    monkeypatch.setattr(drive, "_get_file_metadata", lambda *a: {
        "permissions": [{"id": "synthetic-owner", "type": "user", "role": "owner"}]})
    monkeypatch.setattr(drive, "_verify_remote_artifact", lambda *a: None)
    monkeypatch.setattr(drive, "_verify_folder", lambda *a, **k: {})
    monkeypatch.setattr(drive, "_book_folder_acl", lambda *a: None)
    proof = drive.finalize_drive_job(
        result, source_dir, binding, job_id=job.job_id,
        profile="book_single_atom", job_hash=job.job_hash)
    assert proof["status"] == "PUBLISHED_VERIFIED"
    assert proof["profile"] == "book_single_atom"
    assert "comparison_required" not in proof
    assert len(marker) == 1 and "comparison_required" not in marker[0]
    assert result.exists() and source_dir.exists()
