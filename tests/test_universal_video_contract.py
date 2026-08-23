from pathlib import Path

import pytest

from universal_video.contract import VideoContractError, canonical_job_hash, validate_job
from universal_video.profiles import PROFILES


def test_diana_is_project_metadata_not_a_special_profile(tmp_path: Path):
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"placeholder")
    job = validate_job(
        {
            "job_id": "diana-004",
            "profile": "bridge_lesson",
            "project": "diana_250",
            "source": {"kind": "local_path", "path": str(media)},
        },
        allowed_local_root=str(tmp_path),
    )
    assert job.profile == "bridge_lesson"
    assert job.project == "diana_250"
    assert "diana_250" not in PROFILES


def test_local_path_cannot_escape_media_root(tmp_path: Path):
    outside = tmp_path.parent / "outside.mp4"
    with pytest.raises(VideoContractError):
        validate_job(
            {
                "job_id": "x",
                "profile": "transcript_only",
                "source": {"kind": "local_path", "path": str(outside)},
            },
            allowed_local_root=str(tmp_path),
        )


def test_google_drive_source_is_bounded():
    job = validate_job(
        {
            "job_id": "lecture-1",
            "profile": "educational",
            "source": {"kind": "google_drive", "file_id": "1AbCdEfGhIjKlMnOpQrStUvWxYz"},
        }
    )
    assert job.source["kind"] == "google_drive"


def test_hash_is_deterministic(tmp_path: Path):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    payload = {
        "job_id": "same",
        "profile": "transcript_only",
        "source": {"kind": "local_path", "path": str(path)},
    }
    a = validate_job(payload, allowed_local_root=str(tmp_path))
    b = validate_job(payload, allowed_local_root=str(tmp_path))
    assert canonical_job_hash(a) == canonical_job_hash(b)


def test_chunk_bounds(tmp_path: Path):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    with pytest.raises(VideoContractError):
        validate_job(
            {
                "job_id": "bad-chunk",
                "profile": "transcript_only",
                "source": {"kind": "local_path", "path": str(path)},
                "options": {"chunk_seconds": 5000},
            },
            allowed_local_root=str(tmp_path),
        )
