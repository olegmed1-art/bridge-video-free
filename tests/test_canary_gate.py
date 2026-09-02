from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from universal_video.canary_gate import (
    CanaryGateError,
    apply_result_contract,
    canonical_json_bytes,
    source_identity_from_job,
    validate_image_digest,
    validate_runtime_sha,
    verify_source_snapshot,
)

RUNTIME_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
OUTPUT_FOLDER = "output-folder"
MASTER_ID = "master-pdf-id"
MASTER_BYTES = b"%PDF-1.7\nverified canary artifact\n%%EOF\n"
MASTER_SHA = hashlib.sha256(MASTER_BYTES).hexdigest()


def job(**changes):
    payload = {
        "id": "11111111-1111-1111-1111-111111111111",
        "stable_job_key": "uv-canary-test",
        "source_file_id": "source-video-id",
        "source_name": "Диана 13.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 696002235,
        "source_folder_id": "source-folder",
        "source_checksum": "md5:" + "1" * 32,
        "output_folder_id": OUTPUT_FOLDER,
    }
    payload.update(changes)
    return payload


def processor_result(**changes):
    payload = {
        "processor": "stable_review_hardened",
        "verdict": "REVIEW_READY",
        "publication_state": "NOT_PUBLISHED",
        "canonical_promotion_allowed": False,
        "master_pdf_drive_id": MASTER_ID,
        "master_pdf_sha256": MASTER_SHA,
        "pages": 3,
    }
    payload.update(changes)
    return payload


class FakeDrive:
    def __init__(self):
        self.files = {
            MASTER_ID: {
                "bytes": MASTER_BYTES,
                "metadata": {
                    "id": MASTER_ID,
                    "name": "masterPdf.pdf",
                    "mimeType": "application/pdf",
                    "size": str(len(MASTER_BYTES)),
                    "parents": [OUTPUT_FOLDER],
                    "trashed": False,
                },
            }
        }
        self.manifest_id = "manifest-id"
        self.fail_ids: set[str] = set()
        self.corrupt_ids: set[str] = set()
        self.upload_sha_override: str | None = None

    def upload(self, *, local_path: Path, parent_id: str, remote_name: str, token: str):
        assert token == "test-token"
        assert parent_id == OUTPUT_FOLDER
        payload = local_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        self.files[self.manifest_id] = {
            "bytes": payload,
            "metadata": {
                "id": self.manifest_id,
                "name": remote_name,
                "mimeType": "application/json",
                "size": str(len(payload)),
                "parents": [parent_id],
                "trashed": False,
            },
        }
        return {
            "file_id": self.manifest_id,
            "sha256": self.upload_sha_override or digest,
            "size_bytes": len(payload),
        }

    def download(self, *, file_id: str, destination: Path, token: str, expected_checksum: str):
        assert token == "test-token"
        if file_id in self.fail_ids:
            raise OSError("simulated Drive readback failure")
        entry = self.files[file_id]
        payload = entry["bytes"]
        if file_id in self.corrupt_ids:
            payload += b"corrupt"
        observed = hashlib.sha256(payload).hexdigest()
        if expected_checksum != f"sha256:{observed}":
            raise RuntimeError("simulated checksum mismatch")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        metadata = dict(entry["metadata"])
        metadata["size"] = str(len(payload))
        return metadata


def apply(fake: FakeDrive, **changes):
    arguments = {
        "job": job(),
        "processor_result": processor_result(),
        "runtime_sha": RUNTIME_SHA,
        "image_digest": IMAGE_DIGEST,
        "token": "test-token",
        "downloader": fake.download,
        "uploader": fake.upload,
    }
    arguments.update(changes)
    return apply_result_contract(**arguments)


def test_happy_path_requires_real_artifact_and_manifest_readback():
    fake = FakeDrive()
    result = apply(fake)

    assert result["publication_state"] == "NOT_PUBLISHED"
    assert result["canonical_promotion_allowed"] is False
    assert result["terminal_receipt"]["runtime_sha"] == RUNTIME_SHA
    assert result["terminal_receipt"]["image_digest"] == IMAGE_DIGEST
    assert result["terminal_receipt"]["drive_readback_verified"] is True
    assert [item["kind"] for item in result["artifact_locators"]] == [
        "masterPdf",
        "artifactManifest",
    ]
    assert all(item["readback_verified"] for item in result["artifact_locators"])
    assert fake.files[fake.manifest_id]["bytes"].endswith(b"\n")


def test_source_checksum_is_mandatory():
    with pytest.raises(CanaryGateError, match="checksum"):
        source_identity_from_job(job(source_checksum=""))


def test_source_snapshot_requires_exact_live_identity_and_checksum():
    expected = {
        "file_id": "source-video-id",
        "name": "Диана 13.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 696002235,
        "parent_id": "source-folder",
        "checksum": "md5:" + "1" * 32,
    }
    metadata = {
        "id": expected["file_id"],
        "name": expected["name"],
        "mimeType": expected["mime_type"],
        "size": str(expected["size_bytes"]),
        "parents": [expected["parent_id"]],
        "md5Checksum": "1" * 32,
        "trashed": False,
    }
    assert verify_source_snapshot(expected, metadata)["checksum"] == expected["checksum"]
    assert verify_source_snapshot(dict(expected, checksum="AUTO"), metadata)["checksum"] == expected["checksum"]
    metadata["md5Checksum"] = "2" * 32
    with pytest.raises(CanaryGateError, match="checksum mismatch"):
        verify_source_snapshot(expected, metadata)


def test_unreadable_master_artifact_cannot_pass():
    fake = FakeDrive()
    fake.fail_ids.add(MASTER_ID)
    with pytest.raises(CanaryGateError, match="Drive readback failed"):
        apply(fake)


def test_master_checksum_mismatch_cannot_pass():
    fake = FakeDrive()
    fake.corrupt_ids.add(MASTER_ID)
    with pytest.raises(CanaryGateError, match="Drive readback failed"):
        apply(fake)


def test_failed_manifest_readback_cannot_pass():
    fake = FakeDrive()
    fake.fail_ids.add(fake.manifest_id)
    with pytest.raises(CanaryGateError, match="Drive readback failed"):
        apply(fake)


def test_manifest_upload_receipt_mismatch_cannot_pass():
    fake = FakeDrive()
    fake.upload_sha_override = "0" * 64
    with pytest.raises(CanaryGateError, match="upload receipt SHA-256 mismatch"):
        apply(fake)


def test_missing_result_locator_cannot_pass():
    fake = FakeDrive()
    with pytest.raises(CanaryGateError, match="no master PDF Drive locator"):
        apply(fake, processor_result=processor_result(master_pdf_drive_id=""))


def test_published_or_promoted_result_cannot_pass():
    fake = FakeDrive()
    with pytest.raises(CanaryGateError, match="NOT_PUBLISHED"):
        apply(fake, processor_result=processor_result(publication_state="PUBLISHED"))
    with pytest.raises(CanaryGateError, match="canonical promotion"):
        apply(fake, processor_result=processor_result(canonical_promotion_allowed=True))


@pytest.mark.parametrize("value", ["", "a" * 39, "g" * 40, "A" * 40])
def test_runtime_sha_is_exact(value):
    with pytest.raises(CanaryGateError):
        validate_runtime_sha(value)


@pytest.mark.parametrize(
    "value",
    ["", "latest", "sha256:" + "b" * 63, "SHA256:" + "b" * 64, "b" * 64],
)
def test_image_digest_is_exact(value):
    with pytest.raises(CanaryGateError):
        validate_image_digest(value)


def test_manifest_serialization_is_canonical():
    assert canonical_json_bytes({"z": 1, "a": "я"}) == '{"a":"я","z":1}\n'.encode("utf-8")
