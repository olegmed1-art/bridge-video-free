"""Synthetic source-boundary and recovery regressions; no real external writes."""
import copy
import hashlib
import json

import pytest

from universal_video import drive_results as outputs, durable_drive as durable
from universal_video.contract import canonical_job_hash, validate_job
from universal_video.result_conformance import verify_result
from universal_video.server_review import build_server_review
from test_universal_video_comparison_artifacts import prepared, attach, write
from test_universal_video_result_conformance import _bundle, _fingerprint
from test_universal_video_durable_drive import setup as drive_setup


def legacy_fixture(tmp_path, required):
    job, manifest = _bundle(tmp_path)
    request = {"job_id": manifest["job_id"], "profile": manifest["profile"],
               "source": {"kind": "google_drive", "file_id": manifest["source"]["file_id"]}}
    if required is not None:
        request["metadata"] = {"comparison_required": required}
        manifest["metadata"] = copy.deepcopy(request["metadata"])
    manifest["job_hash"] = canonical_job_hash(validate_job(request))
    write(job / "manifest.json", manifest)
    # Let collection reach the policy boundary; this placeholder grants no review.
    write(job / "server_review.json", {"schema": "synthetic-test-placeholder", "state": "NOT_REVIEWED"})
    args = {"expected_job_id": manifest["job_id"], "expected_profile": manifest["profile"],
            "expected_job_hash": manifest["job_hash"],
            "expected_source_file_id": manifest["source"]["file_id"],
            "expected_artifact_set_sha256": "f" * 64, "expected_job_payload": request}
    return job, manifest, request, args


def forbid_drive(monkeypatch):
    for name in ("access_token", "probe_destination", "_create_folder", "_upload_or_verify_file"):
        monkeypatch.setattr(outputs, name, lambda *a, **k: pytest.fail("legacy refusal reached Drive"))


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("strip", ["metadata", "declaration", "both"])
def test_legacy_requires_trusted_policy_after_result_stripping(tmp_path, monkeypatch, dry_run, strip):
    job, manifest, request, args = legacy_fixture(tmp_path, True)
    manifest["comparison_artifacts"] = None
    if strip in {"metadata", "both"}:
        manifest.pop("metadata")
    if strip in {"declaration", "both"}:
        manifest.pop("comparison_artifacts")
    write(job / "manifest.json", manifest)
    forbid_drive(monkeypatch)
    with pytest.raises(RuntimeError, match="comparison"):
        outputs.publish_result(job, "synthetic_destination", dry_run=dry_run, **args)
    assert not (job / "DURABLE_PUBLICATION_PROOF.json").exists()


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("substitution", ["missing", "false"])
def test_legacy_refuses_missing_or_downgraded_trusted_request(tmp_path, monkeypatch, dry_run, substitution):
    job, manifest, request, args = legacy_fixture(tmp_path, True)
    manifest.pop("metadata")
    write(job / "manifest.json", manifest)
    args["expected_job_payload"] = None if substitution == "missing" else {
        **request, "metadata": {"comparison_required": False}}
    forbid_drive(monkeypatch)
    with pytest.raises(RuntimeError, match="trusted job request"):
        outputs.publish_result(job, "synthetic_destination", dry_run=dry_run, **args)


@pytest.mark.parametrize("required", [None, False])
def test_ordinary_legacy_dry_run_accepts_matching_trusted_request(tmp_path, monkeypatch, required):
    job, manifest, request, args = legacy_fixture(tmp_path, required)
    (job / "server_review.json").unlink()  # positive path must build a real review
    report = verify_result(job, **{k: args[k] for k in (
        "expected_job_id", "expected_profile", "expected_job_hash", "expected_source_file_id")},
        evidence_phase="GENERATION_FINALIZATION")
    write(job / "server_review.json", build_server_review(job, report))
    args["expected_artifact_set_sha256"] = outputs.artifact_set_sha256(outputs.collect_compact_artifacts(job))
    forbid_drive(monkeypatch)
    assert outputs.publish_result(job, "synthetic_destination", dry_run=True, **args)["status"] == "DRY_RUN_READY"


@pytest.mark.parametrize("dry_run", [False, True])
def test_legacy_cli_uses_frozen_request_when_output_flags_are_stripped(tmp_path, monkeypatch, dry_run):
    job, manifest, request, args = legacy_fixture(tmp_path, True)
    manifest.pop("metadata")
    write(job / "manifest.json", manifest)
    request_path = tmp_path / "trusted-request.json"
    write(request_path, request)
    argv = ["drive-results", "publish", "--job-dir", str(job),
            "--folder-id", "synthetic_destination",
            "--expected-job-id", args["expected_job_id"], "--expected-profile", args["expected_profile"],
            "--expected-job-hash", args["expected_job_hash"],
            "--expected-source-file-id", args["expected_source_file_id"],
            "--expected-artifact-set-sha256", args["expected_artifact_set_sha256"],
            "--job-request", str(request_path)]
    if dry_run:
        argv.append("--dry-run")
    forbid_drive(monkeypatch)
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(RuntimeError, match="requires protected durable"):
        outputs.main()


def test_required_recovery_uses_real_review_finalization_and_cleanup(drive_setup, tmp_path, monkeypatch):
    pytest.importorskip("fcntl")
    from universal_video import spool_worker as worker, drive_stage as stage, drive_adapter as adapter
    import test_universal_video_durable_drive as protocol
    result_unused, source_unused, backend, binding = drive_setup
    content = b"synthetic-v" * (128 * 1024)
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(protocol, "CONTENT", content)
    monkeypatch.setattr(protocol, "SHA", digest)
    backend.meta.update(size=str(len(content)), sha256Checksum=digest)
    spool = tmp_path / "spool"
    for name in ("inbox", "results"):
        (spool / name).mkdir(parents=True, exist_ok=True)
    seed, manifest = _bundle(tmp_path / "seed")
    result = spool / "results" / manifest["job_id"]
    seed.rename(result)
    request = {"job_id": manifest["job_id"], "profile": manifest["profile"],
               "source": {"kind": "google_drive", "file_id": protocol.SOURCE},
               "metadata": {"comparison_required": True}}
    job_hash = canonical_job_hash(validate_job(request))
    source_fp = _fingerprint({"kind": "google_drive", "file_id": protocol.SOURCE,
                             "size_bytes": len(content), "checksum_kind": "sha256Checksum",
                             "checksum": digest})
    manifest.update(job_hash=job_hash, metadata=request["metadata"], source_fingerprint=source_fp,
                    source_fingerprint_basis="sha256Checksum+size+file_id")
    manifest["source"] = {"kind": "google_drive", "file_id": protocol.SOURCE,
                          "size": str(len(content)), "version": "1", "sha256Checksum": digest,
                          "fingerprint": source_fp, "fingerprint_basis": "sha256Checksum+size+file_id",
                          "reuse_safe": True}
    manifest["media"].update(sha256=digest, size_bytes=len(content))
    attach(prepared(tmp_path, parent=(result, manifest)), with_review=False)
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("UNIVERSAL_VIDEO_MEDIA_ROOT", str(media))
    registry = tmp_path / "bindings.json"
    write(registry, {"schema": "universal-video-drive-bindings-v1", "jobs": [{
        "job_id": request["job_id"], "job_hash": job_hash, "source_file_id": protocol.SOURCE,
        "processing_upload_authorized": True, "folders": protocol.FOLDERS}]})
    registry.chmod(0o600)
    monkeypatch.setenv("UNIVERSAL_VIDEO_DRIVE_BINDINGS_FILE", str(registry))
    monkeypatch.setattr(stage, "access_token", lambda: "synthetic-token")
    # Restore the actual conformance checker replaced by the protocol fixture.
    # Worker review, durable finalization and cleanup functions remain real.
    monkeypatch.setattr(durable, "verify_result", verify_result)
    monkeypatch.setattr(worker, "run_job", lambda *a: pytest.fail("recovery repeated compute"))
    monkeypatch.setattr(worker, "validate_video_runtime", lambda: pytest.fail("retained recovery entered compute preflight"))
    assert worker.verify_result is verify_result
    assert worker.build_server_review is build_server_review
    assert worker.finalize_drive_job is durable.finalize_drive_job
    assert worker.cleanup_proof_matches is durable.cleanup_proof_matches
    inbox = spool / "inbox" / (request["job_id"] + ".json")
    write(inbox, request)
    backend.fail_readback = True
    assert worker.process_one(spool)
    source = media / "drive-ready" / request["job_id"]
    review_before = (result / "server_review.json").read_bytes()
    assert source.exists() and inbox.exists()
    assert not (spool / "done" / inbox.name).exists()
    assert backend.files  # interrupted after actual fake upload
    retry_path = spool / "progress" / (request["job_id"] + ".recovery.json")
    retry = durable.read_receipt(retry_path)
    retry["retry_after_unix"] = 0
    durable.atomic_json(retry_path, retry)
    backend.fail_readback = False
    assert worker.process_one(spool)
    assert (result / "server_review.json").read_bytes() == review_before
    assert (spool / "done" / inbox.name).exists()
    assert not source.exists()  # existing narrow local-copy cleanup, not result deletion
    proof = durable.read_receipt(result / durable.FINAL_RECEIPT)
    assert proof["comparison_required"] is True
    assert proof["status"] == "PUBLISHED_VERIFIED"
    assert proof["remote_verification"] == durable.READBACK
    remote_ids = [r["file_id"] for r in proof["remote_artifacts"]] + [proof["remote_receipt"]["file_id"]]
    assert len(remote_ids) == len(set(remote_ids)) == len(backend.files)
    assert (result / "comparison/index.json").exists()
