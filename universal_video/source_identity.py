"""Fail-closed Google Drive source identity checks for one video job."""
from __future__ import annotations

import re
from typing import Any, Mapping

from bridge_worker_3_1_free import stable_job_id

from .drive_adapter import file_metadata

_CHECKSUMS = (
    ("sha256Checksum", "sha256", 64),
    ("sha1Checksum", "sha1", 40),
    ("md5Checksum", "md5", 32),
)
_CHECKSUM_RE = re.compile(r"^(sha256:[0-9a-f]{64}|sha1:[0-9a-f]{40}|md5:[0-9a-f]{32})$")


class SourceIdentityError(RuntimeError):
    """Source metadata is missing, ambiguous, or different from the claim."""

    error_code = "UV_SOURCE_IDENTITY_FAILED"


def metadata_checksum(metadata: Mapping[str, Any], *, required: bool = True) -> str | None:
    """Return the strongest valid provider checksum exposed by Drive."""

    for key, label, length in _CHECKSUMS:
        value = str(metadata.get(key) or "").strip().lower()
        if not value:
            continue
        if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
            raise SourceIdentityError("SOURCE_CHECKSUM_METADATA_INVALID")
        return f"{label}:{value}"
    if required:
        raise SourceIdentityError("SOURCE_CHECKSUM_METADATA_MISSING")
    return None


def normalize_source_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize exactly the six identity fields required before processing."""

    try:
        size_bytes = int(metadata.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise SourceIdentityError("SOURCE_SIZE_INVALID") from exc
    parents = [str(value) for value in (metadata.get("parents") or [])]
    identity = {
        "file_id": str(metadata.get("id") or "").strip(),
        "name": str(metadata.get("name") or "").strip(),
        "mime_type": str(metadata.get("mimeType") or "").strip(),
        "size_bytes": size_bytes,
        "parent_id": parents[0] if len(parents) == 1 else "",
        "checksum": metadata_checksum(metadata, required=True),
    }
    if (
        not identity["file_id"]
        or not identity["name"]
        or not identity["mime_type"].startswith("video/")
        or identity["size_bytes"] <= 0
        or len(parents) != 1
        or not _CHECKSUM_RE.fullmatch(str(identity["checksum"]))
    ):
        raise SourceIdentityError("SOURCE_IDENTITY_METADATA_INVALID")
    return identity


def normalize_expected_identity(identity: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a previously captured exact source passport."""

    try:
        size_bytes = int(identity.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise SourceIdentityError("EXPECTED_SOURCE_SIZE_INVALID") from exc
    result = {
        "file_id": str(identity.get("file_id") or "").strip(),
        "name": str(identity.get("name") or "").strip(),
        "mime_type": str(identity.get("mime_type") or "").strip(),
        "size_bytes": size_bytes,
        "parent_id": str(identity.get("parent_id") or "").strip(),
        "checksum": str(identity.get("checksum") or "").strip().lower(),
    }
    if (
        not result["file_id"]
        or not result["name"]
        or not result["mime_type"].startswith("video/")
        or result["size_bytes"] <= 0
        or not result["parent_id"]
        or not _CHECKSUM_RE.fullmatch(result["checksum"])
    ):
        raise SourceIdentityError("EXPECTED_SOURCE_IDENTITY_INVALID")
    return result


def read_source_identity(file_id: str, token: str) -> dict[str, Any]:
    return normalize_source_metadata(file_metadata(file_id, token))


def verify_expected_source_identity(expected: Mapping[str, Any], token: str) -> dict[str, Any]:
    normalized = normalize_expected_identity(expected)
    observed = read_source_identity(normalized["file_id"], token)
    if observed != normalized:
        raise SourceIdentityError("SOURCE_IDENTITY_READBACK_MISMATCH")
    return observed


def verify_claimed_source_identity(claim: Mapping[str, Any], token: str) -> dict[str, Any]:
    """Re-read Drive metadata and compare it byte-for-byte with the leased claim."""

    source_file_id = str(claim.get("source_file_id") or "")
    expected = normalize_expected_identity({
        "file_id": source_file_id,
        "name": claim.get("source_name"),
        "mime_type": claim.get("source_mime_type"),
        "size_bytes": claim.get("source_size_bytes"),
        "parent_id": claim.get("source_folder_id"),
        "checksum": claim.get("source_checksum"),
    })
    if str(claim.get("stable_job_key") or "") != stable_job_id("drive", source_file_id):
        raise SourceIdentityError("SOURCE_STABLE_JOB_KEY_MISMATCH")
    return verify_expected_source_identity(expected, token)


__all__ = [
    "SourceIdentityError",
    "metadata_checksum",
    "normalize_expected_identity",
    "normalize_source_metadata",
    "read_source_identity",
    "verify_claimed_source_identity",
    "verify_expected_source_identity",
]
