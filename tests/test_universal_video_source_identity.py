from __future__ import annotations

import pytest

from universal_video import source_identity as subject


def _metadata(**overrides):
    value = {
        "id": "file000000000001",
        "name": "Lesson 13.mp4",
        "mimeType": "video/mp4",
        "size": "696237577",
        "parents": ["folder000000001"],
        "md5Checksum": "a" * 32,
    }
    value.update(overrides)
    return value


def test_normalize_source_metadata_requires_all_six_fields():
    result = subject.normalize_source_metadata(_metadata())
    assert result == {
        "file_id": "file000000000001",
        "name": "Lesson 13.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 696237577,
        "parent_id": "folder000000001",
        "checksum": "md5:" + "a" * 32,
    }


def test_normalize_source_metadata_rejects_missing_checksum():
    with pytest.raises(subject.SourceIdentityError, match="CHECKSUM.*MISSING"):
        subject.normalize_source_metadata(_metadata(md5Checksum=""))


def test_verify_expected_source_identity_fails_closed_on_change(monkeypatch):
    expected = subject.normalize_source_metadata(_metadata())
    monkeypatch.setattr(subject, "file_metadata", lambda *_: _metadata(size="696237578"))
    with pytest.raises(subject.SourceIdentityError, match="READBACK_MISMATCH"):
        subject.verify_expected_source_identity(expected, "token")


def test_verify_claimed_source_identity_checks_stable_key(monkeypatch):
    monkeypatch.setattr(subject, "file_metadata", lambda *_: _metadata())
    claim = {
        "source_file_id": "file000000000001",
        "source_name": "Lesson 13.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 696237577,
        "source_folder_id": "folder000000001",
        "source_checksum": "md5:" + "a" * 32,
        "stable_job_key": "wrong",
    }
    with pytest.raises(subject.SourceIdentityError, match="STABLE_JOB_KEY"):
        subject.verify_claimed_source_identity(claim, "token")
