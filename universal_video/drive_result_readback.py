"""Actual Google Drive readback contract for routed Universal Video results."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Mapping

import requests

from .drive_adapter import DRIVE, file_metadata
from .source_identity import metadata_checksum, normalize_expected_identity

MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 512 * 1024 * 1024
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class DriveResultContractError(RuntimeError):
    """The result cannot be proved readable and identity-bound."""

    error_code = "UV_DRIVE_READBACK_FAILED"


def _headers(token: str) -> dict[str, str]:
    if not token:
        raise DriveResultContractError("DRIVE_TOKEN_MISSING")
    return {"Authorization": f"Bearer {token}"}


def _escape_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _list_exact_name(folder_id: str, name: str, token: str) -> dict[str, Any]:
    response = requests.get(
        f"{DRIVE}/files",
        headers=_headers(token),
        params={
            "q": (
                f"'{_escape_query(folder_id)}' in parents and "
                f"name='{_escape_query(name)}' and trashed=false"
            ),
            "fields": (
                "files(id,name,mimeType,size,parents,md5Checksum,"
                "sha1Checksum,sha256Checksum),nextPageToken"
            ),
            "pageSize": 10,
            "spaces": "drive",
        },
        timeout=30,
    )
    try:
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise DriveResultContractError("DRIVE_RESULT_LIST_FAILED") from exc
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list) or len(files) != 1 or payload.get("nextPageToken"):
        raise DriveResultContractError("DRIVE_RESULT_LOCATOR_NOT_UNIQUE")
    item = files[0]
    if not isinstance(item, dict):
        raise DriveResultContractError("DRIVE_RESULT_LOCATOR_INVALID")
    return dict(item)


def _stream_readback(
    file_id: str,
    token: str,
    *,
    max_bytes: int,
    collect: bool,
) -> tuple[dict[str, Any], bytes | None]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    sha1 = hashlib.sha1(usedforsecurity=False)
    body = bytearray() if collect else None
    size = 0
    try:
        with requests.get(
            f"{DRIVE}/files/{file_id}",
            headers=_headers(token),
            params={"alt": "media"},
            stream=True,
            timeout=180,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_content(1024 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise DriveResultContractError("DRIVE_RESULT_READBACK_TOO_LARGE")
                sha256.update(chunk)
                md5.update(chunk)
                sha1.update(chunk)
                if body is not None:
                    body.extend(chunk)
    except DriveResultContractError:
        raise
    except Exception as exc:
        raise DriveResultContractError("DRIVE_RESULT_MEDIA_READBACK_FAILED") from exc
    if size <= 0:
        raise DriveResultContractError("DRIVE_RESULT_READBACK_EMPTY")
    return {
        "size_bytes": size,
        "sha256": sha256.hexdigest(),
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
    }, bytes(body) if body is not None else None


def readback_artifact(
    metadata: Mapping[str, Any],
    *,
    token: str,
    expected_parent_id: str,
    expected_name: str,
    expected_mime_type: str,
    expected_sha256: str | None = None,
    max_bytes: int,
    collect: bool = False,
) -> tuple[dict[str, Any], bytes | None]:
    """Read the bytes back and bind them to provider metadata and expectations."""

    file_id = str(metadata.get("id") or "").strip()
    if not file_id:
        raise DriveResultContractError("DRIVE_RESULT_FILE_ID_MISSING")
    # Do not trust a prior listing alone; get metadata again immediately before bytes.
    current = file_metadata(file_id, token)
    try:
        declared_size = int(current.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise DriveResultContractError("DRIVE_RESULT_SIZE_INVALID") from exc
    parents = [str(value) for value in (current.get("parents") or [])]
    provider_checksum = metadata_checksum(current, required=True)
    if (
        str(current.get("id") or "") != file_id
        or str(current.get("name") or "") != expected_name
        or str(current.get("mimeType") or "") != expected_mime_type
        or parents != [expected_parent_id]
        or declared_size <= 0
        or declared_size > max_bytes
    ):
        raise DriveResultContractError("DRIVE_RESULT_METADATA_MISMATCH")
    measured, body = _stream_readback(file_id, token, max_bytes=max_bytes, collect=collect)
    if measured["size_bytes"] != declared_size:
        raise DriveResultContractError("DRIVE_RESULT_SIZE_READBACK_MISMATCH")
    if provider_checksum.startswith("md5:") and measured["md5"] != provider_checksum.split(":", 1)[1]:
        raise DriveResultContractError("DRIVE_RESULT_PROVIDER_CHECKSUM_MISMATCH")
    if provider_checksum.startswith("sha256:") and measured["sha256"] != provider_checksum.split(":", 1)[1]:
        raise DriveResultContractError("DRIVE_RESULT_PROVIDER_CHECKSUM_MISMATCH")
    if provider_checksum.startswith("sha1:") and measured["sha1"] != provider_checksum.split(":", 1)[1]:
        raise DriveResultContractError("DRIVE_RESULT_PROVIDER_CHECKSUM_MISMATCH")
    if expected_sha256 is not None:
        normalized = str(expected_sha256).strip().lower()
        if not _SHA256_RE.fullmatch(normalized) or measured["sha256"] != normalized:
            raise DriveResultContractError("DRIVE_RESULT_EXPECTED_CHECKSUM_MISMATCH")
    return {
        "drive_id": file_id,
        "name": expected_name,
        "mime_type": expected_mime_type,
        "size_bytes": measured["size_bytes"],
        "provider_checksum": provider_checksum,
        "sha256": measured["sha256"],
        "parent_folder_id": expected_parent_id,
        "readback_verified": True,
    }, body


def _json_artifact(
    folder_id: str,
    name: str,
    token: str,
    *,
    role: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata = _list_exact_name(folder_id, name, token)
    locator, body = readback_artifact(
        metadata,
        token=token,
        expected_parent_id=folder_id,
        expected_name=name,
        expected_mime_type="application/json",
        max_bytes=MAX_JSON_BYTES,
        collect=True,
    )
    try:
        payload = json.loads((body or b"").decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DriveResultContractError("DRIVE_RESULT_JSON_UNREADABLE") from exc
    if not isinstance(payload, dict):
        raise DriveResultContractError("DRIVE_RESULT_JSON_NOT_OBJECT")
    locator["role"] = role
    return locator, payload


def _runtime_identity() -> tuple[str, str]:
    commit = os.getenv("UNIVERSAL_VIDEO_SOURCE_COMMIT", "").strip().lower()
    image = os.getenv("UNIVERSAL_VIDEO_IMAGE_DIGEST", "").strip().lower()
    if not _COMMIT_RE.fullmatch(commit) or not _IMAGE_RE.fullmatch(image):
        raise DriveResultContractError("RUNTIME_IMAGE_IDENTITY_INVALID")
    return commit, image


def _hash_object(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def verify_routed_result_contract(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    token: str,
    source_identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless every routed artifact is actually readable again."""

    runtime_sha, image_digest = _runtime_identity()
    source = normalize_expected_identity(source_identity)
    folder_id = str(claim.get("output_folder_id") or "").strip()
    stable_job_key = str(claim.get("stable_job_key") or "").strip()
    revision = str(claim.get("algorithm_revision") or "").strip()
    if (
        not folder_id
        or source["file_id"] != str(claim.get("source_file_id") or "")
        or stable_job_key != str(done.get("job_id") or "")
        or revision != str(done.get("algorithmRevision") or "")
    ):
        raise DriveResultContractError("DRIVE_RESULT_ROOT_IDENTITY_MISMATCH")

    master = done.get("masterPdf")
    if not isinstance(master, Mapping):
        raise DriveResultContractError("DRIVE_RESULT_MASTER_LOCATOR_MISSING")
    master_id = str(master.get("driveId") or "").strip()
    master_name = str(master.get("name") or "").strip()
    master_sha = str(master.get("sha256") or "").strip().lower()
    if not master_id or not master_name or not _SHA256_RE.fullmatch(master_sha):
        raise DriveResultContractError("DRIVE_RESULT_MASTER_LOCATOR_INVALID")
    master_meta = file_metadata(master_id, token)
    master_locator, _ = readback_artifact(
        master_meta,
        token=token,
        expected_parent_id=folder_id,
        expected_name=master_name,
        expected_mime_type="application/pdf",
        expected_sha256=master_sha,
        max_bytes=MAX_PDF_BYTES,
        collect=False,
    )
    master_locator["role"] = "master_pdf"

    ai_name = f"AI_DONE_{stable_job_key}.json"
    methodology_name = f"METHODOLOGY_READY_{stable_job_key}.json"
    cleanup_name = f"CLEANUP_ACK_{stable_job_key}.json"
    ai_locator, ai_done = _json_artifact(folder_id, ai_name, token, role="ai_done")
    methodology_locator, methodology = _json_artifact(
        folder_id, methodology_name, token, role="methodology_ready"
    )
    cleanup_locator, cleanup = _json_artifact(folder_id, cleanup_name, token, role="cleanup_ack")

    ai_master = ai_done.get("masterPdf") if isinstance(ai_done.get("masterPdf"), Mapping) else {}
    ai_original = ai_done.get("original") if isinstance(ai_done.get("original"), Mapping) else {}
    if (
        ai_done.get("status") != "AI_DONE"
        or ai_done.get("job_id") != stable_job_key
        or ai_done.get("algorithmRevision") != revision
        or ai_original.get("driveId") != source["file_id"]
        or ai_master.get("driveId") != master_id
        or ai_master.get("sha256") != master_sha
    ):
        raise DriveResultContractError("DRIVE_RESULT_AI_DONE_IDENTITY_MISMATCH")
    if (
        methodology.get("status") != "METHODOLOGY_READY"
        or methodology.get("job_id") != stable_job_key
        or methodology.get("algorithmRevision") != revision
        or methodology.get("masterPdfDriveId") != master_id
        or methodology.get("masterPdfSha256") != master_sha
    ):
        raise DriveResultContractError("DRIVE_RESULT_METHODOLOGY_IDENTITY_MISMATCH")
    if (
        cleanup.get("status") != "CLEANUP_ACK"
        or cleanup.get("job_id") != stable_job_key
        or cleanup.get("algorithmRevision") != revision
        or cleanup.get("reportSha256") != master_sha
    ):
        raise DriveResultContractError("DRIVE_RESULT_CLEANUP_IDENTITY_MISMATCH")

    artifacts = sorted(
        [master_locator, ai_locator, methodology_locator, cleanup_locator],
        key=lambda item: str(item["role"]),
    )
    manifest_body: dict[str, Any] = {
        "schema": "universal-video-artifact-manifest-v1",
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "source_identity": source,
        "output_folder_id": folder_id,
        "stable_job_key": stable_job_key,
        "algorithm_revision": revision,
        "artifacts": artifacts,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    manifest_sha = _hash_object(manifest_body)
    manifest = dict(manifest_body, manifest_sha256=manifest_sha)
    terminal_body: dict[str, Any] = {
        "schema": "universal-video-terminal-receipt-v1",
        "status": "REVIEW_READY",
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "source_identity_gate": "PASS",
        "drive_upload_readback_gate": "PASS",
        "artifact_manifest_gate": "PASS",
        "artifact_manifest_sha256": manifest_sha,
        "artifact_locators": artifacts,
        "stable_job_key": stable_job_key,
        "algorithm_revision": revision,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    terminal = dict(terminal_body, terminal_receipt_sha256=_hash_object(terminal_body))
    return {
        "master_pdf_drive_id": master_id,
        "master_pdf_sha256": master_sha,
        "source_identity": source,
        "source_identity_gate": "PASS",
        "drive_upload_readback_gate": "PASS",
        "artifact_manifest_gate": "PASS",
        "artifact_manifest": manifest,
        "artifact_locators": artifacts,
        "terminal_receipt": terminal,
    }


__all__ = [
    "DriveResultContractError",
    "MAX_JSON_BYTES",
    "MAX_PDF_BYTES",
    "readback_artifact",
    "verify_routed_result_contract",
]
