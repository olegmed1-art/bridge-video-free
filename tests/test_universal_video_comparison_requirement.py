"""Synthetic requirement gates only; no real media, credentials or Drive calls."""
import copy

import pytest

from universal_video import durable_drive as durable
from universal_video.comparison_requirement import require_comparison_package
from universal_video.contract import VideoContractError, canonical_job_hash, validate_job
from test_universal_video_comparison_artifacts import prepared, attach, write
from test_universal_video_result_conformance import _bundle, _verify, ResultConformanceError
from test_universal_video_durable_drive import setup as drive_setup, finish
from test_universal_video_durable_drive import full_chain, setup  # full_chain's fixture dependency


def payload():
    return {"job_id": "generic", "profile": "transcript_only",
            "source": {"kind": "google_drive", "file_id": "synthetic_original_01"},
            "metadata": {"comparison_required": True}}


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false", [], {}])
def test_requirement_is_strict_boolean(value):
    request = payload()
    request["metadata"]["comparison_required"] = value
    with pytest.raises(VideoContractError, match="must be a boolean"):
        validate_job(request)


def test_requirement_is_part_of_canonical_identity():
    required = payload()
    ordinary = copy.deepcopy(required)
    ordinary["metadata"] = {}
    assert canonical_job_hash(validate_job(required)) != canonical_job_hash(validate_job(ordinary))
    assert canonical_job_hash(validate_job(required)) == canonical_job_hash(validate_job(copy.deepcopy(required)))


@pytest.mark.parametrize("required", [None, False, True])
def test_conformance_requires_package_only_for_opt_in(tmp_path, required):
    job, manifest = _bundle(tmp_path)
    if required is not None:
        manifest.setdefault("metadata", {})["comparison_required"] = required
    write(job / "manifest.json", manifest)
    if required:
        with pytest.raises(ResultConformanceError, match="comparison artifact"):
            _verify(job)
    else:
        assert _verify(job)["state"] == "PASS"


@pytest.fixture
def required_drive(drive_setup):
    result, source, _, _ = drive_setup
    pin = durable.read_receipt(source / durable.SOURCE_RECEIPT)
    pin["comparison_required"] = True
    durable.atomic_json(source / durable.SOURCE_RECEIPT, pin)
    manifest = durable.read_receipt(result / "manifest.json")
    manifest["metadata"] = {"comparison_required": True}
    durable.atomic_json(result / "manifest.json", manifest)
    return drive_setup


@pytest.mark.parametrize("mode", ["missing", "stripped-flag", "false-flag", "invalid-declaration"])
def test_required_publication_refuses_before_auth_or_upload(required_drive, monkeypatch, mode):
    result, source, drive, _ = required_drive
    manifest = durable.read_receipt(result / "manifest.json")
    if mode == "stripped-flag":
        manifest.pop("metadata")
    elif mode == "false-flag":
        manifest["metadata"]["comparison_required"] = False
    elif mode == "invalid-declaration":
        manifest["comparison_artifacts"] = {}
    durable.atomic_json(result / "manifest.json", manifest)
    monkeypatch.setattr(durable, "access_token", lambda: pytest.fail("auth reached before required package"))
    with pytest.raises(RuntimeError):
        finish(required_drive)
    assert not drive.posts and source.exists()
    assert not (result / durable.FINAL_RECEIPT).exists()


def test_required_real_typed_validator_allows_publication_and_idempotent_retry(required_drive, tmp_path):
    result, source, drive, _ = required_drive
    manifest = durable.read_receipt(result / "manifest.json")
    attach(prepared(tmp_path, parent=(result, manifest)), with_review=False)
    require_comparison_package(result, expected_required=True)
    first = finish(required_drive)
    posts = len(drive.posts)
    assert first["comparison_required"] is True
    assert durable.cleanup_proof_matches(result, source, job_id="job")
    assert durable.cleanup_proof_matches(result, None, job_id="job")
    second = finish(required_drive)
    assert len(drive.posts) == posts
    assert first["artifact_set_sha256"] == second["artifact_set_sha256"]
    assert first["remote_artifacts"] == second["remote_artifacts"]


@pytest.mark.parametrize("mode", ["missing-part", "corrupt-part", "missing-declaration", "stripped-flag"])
def test_required_cleanup_rejects_changed_evidence_with_live_or_saved_pin(required_drive, tmp_path, mode):
    result, source, drive, _ = required_drive
    manifest = durable.read_receipt(result / "manifest.json")
    attach(prepared(tmp_path, parent=(result, manifest)), with_review=False)
    finish(required_drive)
    posts = len(drive.posts)
    if mode == "missing-part":
        (result / "comparison/part-00000.bin").unlink()
    elif mode == "corrupt-part":
        part = result / "comparison/part-00000.bin"
        part.write_bytes(part.read_bytes() + b"x")
    else:
        manifest = durable.read_receipt(result / "manifest.json")
        manifest.pop("comparison_artifacts" if mode == "missing-declaration" else "metadata")
        durable.atomic_json(result / "manifest.json", manifest)
    assert not durable.cleanup_proof_matches(result, source, job_id="job")
    assert not durable.cleanup_proof_matches(result, None, job_id="job")
    assert source.exists() and len(drive.posts) == posts


@pytest.mark.parametrize("required", [None, False])
def test_ordinary_legacy_publication_and_cleanup_remain_allowed(drive_setup, required):
    result, source, _, _ = drive_setup
    if required is not None:
        manifest = durable.read_receipt(result / "manifest.json")
        manifest["metadata"] = {"comparison_required": required}
        durable.atomic_json(result / "manifest.json", manifest)
    finish(drive_setup)
    assert durable.cleanup_proof_matches(result, source, job_id="job")
    assert durable.cleanup_proof_matches(result, None, job_id="job")


def test_required_source_pin_cannot_be_removed_on_stage_reuse(full_chain):
    from universal_video import drive_stage as stage
    job, request, media, source, staged, drive, result, finalize = full_chain
    request["metadata"] = {"comparison_required": True}
    job = validate_job(request)
    pin_path = source / durable.SOURCE_RECEIPT
    pin = durable.read_receipt(pin_path)
    # Same requested hash, but an old receipt cannot silently erase the flag.
    pin["job_hash"] = canonical_job_hash(job)
    pin.pop("comparison_required", None)
    durable.atomic_json(pin_path, pin)
    before = pin_path.read_bytes()
    with pytest.raises(stage.DriveStageError, match="binding changed"):
        stage.stage_drive_job(job, request, media)
    assert pin_path.read_bytes() == before and not drive.posts


def test_new_stage_pin_carries_requirement(full_chain):
    from universal_video import drive_stage as stage
    job, request, media, source, staged, drive, result, finalize = full_chain
    request = copy.deepcopy(request)
    request.update(job_id="required-new", metadata={"comparison_required": True})
    job = validate_job(request)
    staged, directory = stage.stage_drive_job(job, request, media)
    pin = durable.read_receipt(directory / durable.SOURCE_RECEIPT)
    assert pin["comparison_required"] is True
    assert pin["job_hash"] == canonical_job_hash(job)
    assert canonical_job_hash(validate_job(staged)) == pin["job_hash"]


def test_legacy_remote_completion_marker_is_byte_identical_on_upgrade_retry(drive_setup):
    import hashlib
    from universal_video.comparison_artifacts import encoded
    result, source, drive, _ = drive_setup
    first = finish(drive_setup)
    # Construct the pre-gate schema: no comparison_required field in either
    # durable receipt or completion marker. This models an existing remote
    # marker rather than only a fresh publication using the upgraded writer.
    legacy = copy.deepcopy(first)
    legacy.pop("comparison_required", None)
    marker = copy.deepcopy(legacy)
    marker.pop("remote_receipt")
    marker_bytes = encoded(marker)
    marker_id = legacy["remote_receipt"]["file_id"]
    remote = drive.files[marker_id]
    remote.update(content=marker_bytes, size=str(len(marker_bytes)),
                  md5Checksum=hashlib.md5(marker_bytes, usedforsecurity=False).hexdigest())
    legacy["remote_receipt"].update(size_bytes=len(marker_bytes),
                                    sha256=hashlib.sha256(marker_bytes).hexdigest())
    durable.atomic_json(result / durable.FINAL_RECEIPT, legacy)
    posts = len(drive.posts)
    second = finish(drive_setup)
    assert "comparison_required" not in second
    assert len(drive.posts) == posts
    assert second["remote_receipt"]["file_id"] == marker_id
    assert drive.files[marker_id]["content"] == marker_bytes
    assert first["artifact_set_sha256"] == second["artifact_set_sha256"]
    assert durable.cleanup_proof_matches(result, source, job_id="job")
