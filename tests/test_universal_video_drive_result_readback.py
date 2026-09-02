from __future__ import annotations

import hashlib

import pytest

from universal_video import drive_result_readback as subject


class FakeResponse:
    def __init__(self, *, body=b"", payload=None, status=200):
        self.body = body
        self.payload = payload
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self.payload

    def iter_content(self, _size):
        yield self.body


def _meta(body=b"read-back-me"):
    return {
        "id": "result000000001",
        "name": "result.json",
        "mimeType": "application/json",
        "size": str(len(body)),
        "parents": ["output00000001"],
        "md5Checksum": hashlib.md5(body, usedforsecurity=False).hexdigest(),
    }


def test_artifact_is_actually_read_back(monkeypatch):
    body = b"read-back-me"
    monkeypatch.setattr(subject, "file_metadata", lambda *_: _meta(body))
    monkeypatch.setattr(subject.requests, "get", lambda *_, **__: FakeResponse(body=body))
    locator, collected = subject.readback_artifact(
        _meta(body), token="token", expected_parent_id="output00000001",
        expected_name="result.json", expected_mime_type="application/json",
        expected_sha256=hashlib.sha256(body).hexdigest(), max_bytes=1024, collect=True,
    )
    assert collected == body
    assert locator["readback_verified"] is True


def test_failed_media_get_is_not_pass(monkeypatch):
    monkeypatch.setattr(subject, "file_metadata", lambda *_: _meta(b"x"))
    monkeypatch.setattr(subject.requests, "get", lambda *_, **__: FakeResponse(status=503))
    with pytest.raises(subject.DriveResultContractError, match="MEDIA_READBACK_FAILED"):
        subject.readback_artifact(
            _meta(b"x"), token="token", expected_parent_id="output00000001",
            expected_name="result.json", expected_mime_type="application/json", max_bytes=1024,
        )


def test_checksum_mismatch_is_not_pass(monkeypatch):
    metadata = _meta(b"data")
    metadata["md5Checksum"] = "0" * 32
    monkeypatch.setattr(subject, "file_metadata", lambda *_: metadata)
    monkeypatch.setattr(subject.requests, "get", lambda *_, **__: FakeResponse(body=b"data"))
    with pytest.raises(subject.DriveResultContractError, match="PROVIDER_CHECKSUM"):
        subject.readback_artifact(
            metadata, token="token", expected_parent_id="output00000001",
            expected_name="result.json", expected_mime_type="application/json", max_bytes=1024,
        )


def test_duplicate_exact_locator_is_not_pass(monkeypatch):
    monkeypatch.setattr(
        subject.requests, "get",
        lambda *_, **__: FakeResponse(payload={"files": [{"id": "a"}, {"id": "b"}]}),
    )
    with pytest.raises(subject.DriveResultContractError, match="NOT_UNIQUE"):
        subject._list_exact_name("output00000001", "result.json", "token")


def test_manifest_and_terminal_receipt_are_bound_to_one_runtime(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "1" * 40)
    monkeypatch.setenv("UNIVERSAL_VIDEO_IMAGE_DIGEST", "sha256:" + "2" * 64)
    master_sha = "3" * 64
    monkeypatch.setattr(subject, "file_metadata", lambda *_: {"id": "master000000001"})
    monkeypatch.setattr(subject, "readback_artifact", lambda _metadata, **kwargs: ({
        "drive_id": "master000000001", "name": kwargs["expected_name"],
        "mime_type": kwargs["expected_mime_type"], "size_bytes": 99,
        "provider_checksum": "md5:" + "4" * 32, "sha256": master_sha,
        "parent_folder_id": kwargs["expected_parent_id"], "readback_verified": True,
    }, None))

    def json_artifact(_folder, name, _token, *, role):
        locator = {
            "drive_id": role + "-id", "name": name, "mime_type": "application/json",
            "size_bytes": 10, "provider_checksum": "md5:" + "5" * 32,
            "sha256": "6" * 64, "parent_folder_id": "output00000001",
            "readback_verified": True, "role": role,
        }
        common = {"job_id": "job000000000001", "algorithmRevision": "3.1-free-r25.16"}
        if role == "ai_done":
            payload = {**common, "status": "AI_DONE", "original": {"driveId": "source00000001"},
                       "masterPdf": {"driveId": "master000000001", "sha256": master_sha}}
        elif role == "methodology_ready":
            payload = {**common, "status": "METHODOLOGY_READY",
                       "masterPdfDriveId": "master000000001", "masterPdfSha256": master_sha}
        else:
            payload = {**common, "status": "CLEANUP_ACK", "reportSha256": master_sha}
        return locator, payload

    monkeypatch.setattr(subject, "_json_artifact", json_artifact)
    result = subject.verify_routed_result_contract(
        {"output_folder_id": "output00000001", "stable_job_key": "job000000000001",
         "algorithm_revision": "3.1-free-r25.16", "source_file_id": "source00000001"},
        {"job_id": "job000000000001", "algorithmRevision": "3.1-free-r25.16",
         "masterPdf": {"driveId": "master000000001", "name": "master.pdf", "sha256": master_sha}},
        "token",
        {"file_id": "source00000001", "name": "Lesson 13.mp4", "mime_type": "video/mp4",
         "size_bytes": 123, "parent_id": "sourcefolder0001", "checksum": "md5:" + "7" * 32},
    )
    assert result["drive_upload_readback_gate"] == "PASS"
    assert len(result["artifact_manifest"]["artifacts"]) == 4
    assert len(result["artifact_manifest"]["manifest_sha256"]) == 64
    assert result["terminal_receipt"]["status"] == "REVIEW_READY"
    assert result["terminal_receipt"]["canonical_promotion_allowed"] is False
    assert result["terminal_receipt"]["publication_state"] == "NOT_PUBLISHED"


def test_unreadable_json_cannot_create_terminal_receipt(monkeypatch):
    monkeypatch.setenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "1" * 40)
    monkeypatch.setenv("UNIVERSAL_VIDEO_IMAGE_DIGEST", "sha256:" + "2" * 64)
    monkeypatch.setattr(subject, "file_metadata", lambda *_: {"id": "master000000001"})
    monkeypatch.setattr(subject, "readback_artifact", lambda *_, **__: ({
        "drive_id": "master000000001", "name": "master.pdf", "mime_type": "application/pdf",
        "size_bytes": 1, "provider_checksum": "md5:" + "4" * 32, "sha256": "3" * 64,
        "parent_folder_id": "output00000001", "readback_verified": True,
    }, None))
    monkeypatch.setattr(subject, "_json_artifact", lambda *_, **__: (_ for _ in ()).throw(
        subject.DriveResultContractError("JSON_UNREADABLE")))
    with pytest.raises(subject.DriveResultContractError):
        subject.verify_routed_result_contract(
            {"output_folder_id": "output00000001", "stable_job_key": "job000000000001",
             "algorithm_revision": "3.1-free-r25.16", "source_file_id": "source00000001"},
            {"job_id": "job000000000001", "algorithmRevision": "3.1-free-r25.16",
             "masterPdf": {"driveId": "master000000001", "name": "master.pdf", "sha256": "3" * 64}},
            "token",
            {"file_id": "source00000001", "name": "Lesson 13.mp4", "mime_type": "video/mp4",
             "size_bytes": 123, "parent_id": "sourcefolder0001", "checksum": "md5:" + "7" * 32},
        )
