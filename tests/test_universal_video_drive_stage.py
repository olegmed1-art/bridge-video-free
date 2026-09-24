from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from universal_video.contract import validate_job
from universal_video.drive_stage import DriveStageError, remove_staged_job, stage_drive_job


SOURCE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz"
CONTENT = b"v" * (1024 * 1024)


def _payload() -> dict:
    return {
        "job_id": "generic-video-001",
        "profile": "bridge_lesson",
        "source": {"kind": "google_drive", "file_id": SOURCE_ID},
    }


def _named_payload() -> dict:
    payload = _payload()
    payload["source"]["name"] = "request-name.mov"
    return payload


def test_drive_is_fully_verified_before_internal_job_is_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import universal_video.drive_stage as stage

    media = tmp_path / "media"
    media.mkdir()
    metadata = {
        "id": SOURCE_ID,
        "name": "lesson.mp4",
        "mimeType": "video/mp4",
        "size": str(len(CONTENT)),
        "modifiedTime": "2026-08-29T00:00:00Z",
        "md5Checksum": hashlib.md5(CONTENT, usedforsecurity=False).hexdigest(),
    }
    monkeypatch.setattr(stage, "access_token", lambda: "token")
    monkeypatch.setattr(stage, "file_metadata", lambda *_: dict(metadata))

    def download(_file_id, destination, _token, **_kwargs):
        destination.write_bytes(CONTENT)
        return {**metadata, "_download_sha256": hashlib.sha256(CONTENT).hexdigest()}

    monkeypatch.setattr(stage, "download_file", download)
    staged, job_dir = stage_drive_job(validate_job(_payload()), _payload(), media)
    assert staged["source"]["kind"] == "oracle_drive_staged"
    assert staged["source"]["file_id"] == SOURCE_ID
    assert staged["source"]["drive_name"] == "lesson.mp4"
    assert staged["source"]["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert Path(staged["source"]["path"]).read_bytes() == CONTENT
    assert not list(job_dir.glob("*.part"))
    remove_staged_job(job_dir, media)
    assert not job_dir.exists()


def test_drive_metadata_name_does_not_change_request_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import universal_video.drive_stage as stage
    from universal_video.contract import canonical_job_hash

    media = tmp_path / "media"
    media.mkdir()
    metadata = {
        "id": SOURCE_ID,
        "name": "actual-drive-name.mp4",
        "mimeType": "video/mp4",
        "size": str(len(CONTENT)),
    }
    monkeypatch.setattr(stage, "access_token", lambda: "token")
    monkeypatch.setattr(stage, "file_metadata", lambda *_: dict(metadata))

    def download(_file_id, destination, _token, **_kwargs):
        destination.write_bytes(CONTENT)
        return {**metadata, "_download_sha256": hashlib.sha256(CONTENT).hexdigest()}

    monkeypatch.setattr(stage, "download_file", download)
    original_payload = _named_payload()
    original = validate_job(original_payload)
    staged_payload, _ = stage_drive_job(original, original_payload, media)
    staged = validate_job(staged_payload, allowed_local_root=str(media))
    assert staged.source["name"] == "request-name.mov"
    assert staged.source["drive_name"] == "actual-drive-name.mp4"
    assert canonical_job_hash(staged) == canonical_job_hash(original)



def test_truncated_drive_download_never_leaves_part_or_becomes_a_staged_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import universal_video.drive_stage as stage

    media = tmp_path / "media"
    media.mkdir()
    metadata = {
        "id": SOURCE_ID,
        "name": "lesson.mp4",
        "mimeType": "video/mp4",
        "size": str(len(CONTENT)),
    }
    monkeypatch.setattr(stage, "access_token", lambda: "token")
    monkeypatch.setattr(stage, "file_metadata", lambda *_: dict(metadata))

    def truncated_download(_file_id, destination, _token, **_kwargs):
        destination.write_bytes(CONTENT[:-1])
        return dict(metadata)

    monkeypatch.setattr(stage, "download_file", truncated_download)
    with pytest.raises(DriveStageError) as caught:
        stage_drive_job(validate_job(_payload()), _payload(), media)
    assert caught.value.error_code == "UV_DRIVE_SOURCE_SIZE_MISMATCH"
    job_dir = media / "drive-ready" / "generic-video-001"
    assert not (job_dir / "source.mp4").exists()


def test_non_video_drive_mime_is_rejected_before_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import universal_video.drive_stage as stage

    media = tmp_path / "media"
    media.mkdir()
    metadata = {
        "id": SOURCE_ID,
        "name": "not-a-video.pdf",
        "mimeType": "application/pdf",
        "size": str(len(CONTENT)),
    }
    monkeypatch.setattr(stage, "access_token", lambda: "token")
    monkeypatch.setattr(stage, "file_metadata", lambda *_: dict(metadata))
    called = False

    def should_not_download(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("non-video source reached download")

    monkeypatch.setattr(stage, "download_file", should_not_download)
    with pytest.raises(DriveStageError) as caught:
        stage_drive_job(validate_job(_payload()), _payload(), media)
    assert caught.value.error_code == "UV_DRIVE_SOURCE_MIME_UNSUPPORTED"
    assert called is False

def test_cleanup_refuses_path_outside_job_staging(tmp_path: Path):
    media = tmp_path / "media"
    media.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(DriveStageError, match="escapes"):
        remove_staged_job(outside, media)
