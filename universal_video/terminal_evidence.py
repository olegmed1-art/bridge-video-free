"""Fail-closed Drive readback evidence for Universal Video terminal states.

The queue may accept REVIEW_READY only after the routed result objects are read
back from Drive, their identities and checksums are verified, and a canonical
manifest plus artifact locators are produced. This module never publishes or
promotes results and never logs credentials, payloads, URLs, or private paths.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Mapping

import requests

from .drive_adapter import DRIVE, file_metadata

MAX_AI_DONE_BYTES = 2 * 1024 * 1024
MAX_MASTER_PDF_BYTES = 2 * 1024**3
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MD5_RE = re.compile(r"^[0-9a-f]{32}$")
SOURCE_CHECKSUM_RE = re.compile(
    r"^(?:sha256:[0-9a-f]{64}|sha1:[0-9a-f]{40}|md5:[0-9a-f]{32})$"
)
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


class TerminalEvidenceError(RuntimeError):
    """A bounded terminal-evidence failure."""

    def __init__(self, error_code: str) -> None:
        if not re.fullmatch(r"UV_[A-Z0-9_]{1,96}", error_code):
            error_code = "UV_TERMINAL_EVIDENCE_FAILED"
        self.error_code = error_code
        super().__init__(error_code)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _text(value: Any, error_code: str, *, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise TerminalEvidenceError(error_code)
    return result


def _drive_id(value: Any) -> str:
    result = _text(value, "UV_TERMINAL_LOCATOR_INVALID", maximum=200)
    if not DRIVE_ID_RE.fullmatch(result):
        raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_INVALID")
    return result


def _mapping(value: Any, error_code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TerminalEvidenceError(error_code)
    return dict(value)


def source_identity_from_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    """Return the complete immutable source identity required by the gate."""

    try:
        size_bytes = int(claim.get("source_size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise TerminalEvidenceError("UV_SOURCE_IDENTITY_INVALID") from exc
    checksum = str(claim.get("source_checksum") or "").strip().lower()
    if size_bytes <= 0 or not SOURCE_CHECKSUM_RE.fullmatch(checksum):
        raise TerminalEvidenceError("UV_SOURCE_IDENTITY_INVALID")
    return {
        "file_id": _drive_id(claim.get("source_file_id")),
        "name": _text(claim.get("source_name"), "UV_SOURCE_IDENTITY_INVALID"),
        "mime_type": _text(
            claim.get("source_mime_type"),
            "UV_SOURCE_IDENTITY_INVALID",
            maximum=200,
        ),
        "size_bytes": size_bytes,
        "parent_folder_id": _drive_id(claim.get("source_folder_id")),
        "checksum": checksum,
    }


def readback_drive_bytes(
    file_id: str,
    token: str,
    *,
    max_bytes: int,
    retain_body: bool = False,
    metadata_loader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
    get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Read one Drive object and return bounded identity plus content hashes.

    Large result files are verified as a stream. The body is retained only for
    small semantic contracts such as AI_DONE, never for the master PDF.
    """

    file_id = _drive_id(file_id)
    if not token or max_bytes <= 0:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_UNAVAILABLE")
    try:
        metadata = dict(metadata_loader(file_id, token))
        declared_size = int(metadata.get("size") or 0)
    except Exception as exc:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_UNAVAILABLE") from exc
    if metadata.get("id") != file_id or not 0 < declared_size <= max_bytes:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_IDENTITY_MISMATCH")

    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    body = bytearray() if retain_body else None
    observed_size = 0
    try:
        with get(
            f"{DRIVE}/files/{file_id}",
            headers={"Authorization": f"Bearer {token}"},
            params={"alt": "media"},
            stream=True,
            timeout=180,
        ) as response:
            response.raise_for_status()
            for chunk in response.iter_content(8 * 1024 * 1024):
                if not chunk:
                    continue
                observed_size += len(chunk)
                if observed_size > max_bytes:
                    raise TerminalEvidenceError("UV_TERMINAL_READBACK_TOO_LARGE")
                sha256.update(chunk)
                md5.update(chunk)
                if body is not None:
                    body.extend(chunk)
    except TerminalEvidenceError:
        raise
    except Exception as exc:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_UNAVAILABLE") from exc

    if observed_size != declared_size:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_SIZE_MISMATCH")
    actual_sha256 = sha256.hexdigest()
    actual_md5 = md5.hexdigest()
    expected_sha256 = str(metadata.get("sha256Checksum") or "").strip().lower()
    expected_md5 = str(metadata.get("md5Checksum") or "").strip().lower()
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_CHECKSUM_MISMATCH")
    if expected_md5 and actual_md5 != expected_md5:
        raise TerminalEvidenceError("UV_TERMINAL_READBACK_CHECKSUM_MISMATCH")

    result: dict[str, Any] = {
        "file_id": file_id,
        "name": _text(metadata.get("name"), "UV_TERMINAL_READBACK_IDENTITY_MISMATCH"),
        "mime_type": _text(
            metadata.get("mimeType"),
            "UV_TERMINAL_READBACK_IDENTITY_MISMATCH",
            maximum=200,
        ),
        "size_bytes": observed_size,
        "parents": [str(value) for value in (metadata.get("parents") or [])],
        "sha256": actual_sha256,
        "md5": actual_md5,
    }
    if body is not None:
        result["body"] = bytes(body)
    return result


def build_terminal_evidence(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    route_receipt: Mapping[str, Any],
    token: str,
    *,
    readback: Callable[..., Mapping[str, Any]] = readback_drive_bytes,
) -> dict[str, Any]:
    """Return the exact output fields required for a terminal queue receipt."""

    source_identity = source_identity_from_claim(claim)
    source_file_id = source_identity["file_id"]
    stable_job_key = _text(
        claim.get("stable_job_key"),
        "UV_TERMINAL_JOB_IDENTITY_MISMATCH",
        maximum=160,
    )
    algorithm_revision = _text(
        claim.get("algorithm_revision"),
        "UV_TERMINAL_JOB_IDENTITY_MISMATCH",
        maximum=80,
    )
    target_folder_id = _drive_id(claim.get("output_folder_id"))
    if (
        done.get("status") != "AI_DONE"
        or done.get("job_id") != stable_job_key
        or done.get("algorithmRevision") != algorithm_revision
        or (done.get("original") or {}).get("driveId") != source_file_id
    ):
        raise TerminalEvidenceError("UV_TERMINAL_JOB_IDENTITY_MISMATCH")
    if (
        route_receipt.get("stage") != "OUTPUT_ROUTE"
        or route_receipt.get("status") != "ROUTED"
        or route_receipt.get("job_id") != stable_job_key
        or route_receipt.get("target_folder_id") != target_folder_id
        or route_receipt.get("source_untouched") is not True
    ):
        raise TerminalEvidenceError("UV_TERMINAL_ROUTE_UNVERIFIED")

    locators: dict[str, str] = {}
    for item in route_receipt.get("results") or []:
        if not isinstance(item, Mapping):
            raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_INVALID")
        kind = str(item.get("kind") or "")
        if kind in locators:
            raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_INVALID")
        locators[kind] = _drive_id(item.get("file_id"))
    if set(locators) < {"master_pdf", "ai_done"}:
        raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_MISSING")
    if source_file_id in {locators["master_pdf"], locators["ai_done"]}:
        raise TerminalEvidenceError("UV_TERMINAL_SOURCE_RESULT_COLLISION")

    master_contract = done.get("masterPdf") if isinstance(done.get("masterPdf"), Mapping) else {}
    if master_contract.get("driveId") != locators["master_pdf"]:
        raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_MISMATCH")
    expected_master_sha = str(master_contract.get("sha256") or "").strip().lower()
    if not SHA256_RE.fullmatch(expected_master_sha):
        raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID")

    master = dict(
        readback(
            locators["master_pdf"],
            token,
            max_bytes=MAX_MASTER_PDF_BYTES,
            retain_body=False,
        )
    )
    ai_done = dict(
        readback(
            locators["ai_done"],
            token,
            max_bytes=MAX_AI_DONE_BYTES,
            retain_body=True,
        )
    )
    for item in (master, ai_done):
        if item.get("parents") != [target_folder_id]:
            raise TerminalEvidenceError("UV_TERMINAL_RESULT_PARENT_MISMATCH")
        if not SHA256_RE.fullmatch(str(item.get("sha256") or "")):
            raise TerminalEvidenceError("UV_TERMINAL_READBACK_CHECKSUM_MISMATCH")
        if not MD5_RE.fullmatch(str(item.get("md5") or "")):
            raise TerminalEvidenceError("UV_TERMINAL_READBACK_CHECKSUM_MISMATCH")
    if master.get("file_id") != locators["master_pdf"] or master.get("mime_type") != "application/pdf":
        raise TerminalEvidenceError("UV_TERMINAL_MASTER_PDF_IDENTITY_MISMATCH")
    if master.get("sha256") != expected_master_sha:
        raise TerminalEvidenceError("UV_TERMINAL_MASTER_PDF_CHECKSUM_MISMATCH")
    if ai_done.get("file_id") != locators["ai_done"] or ai_done.get("mime_type") != "application/json":
        raise TerminalEvidenceError("UV_TERMINAL_AI_DONE_IDENTITY_MISMATCH")
    try:
        readback_done = json.loads(bytes(ai_done.get("body") or b"").decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TerminalEvidenceError("UV_TERMINAL_AI_DONE_INVALID") from exc
    if _canonical_bytes(readback_done) != _canonical_bytes(dict(done)):
        raise TerminalEvidenceError("UV_TERMINAL_AI_DONE_MISMATCH")

    artifacts = []
    for kind, item in (("master_pdf", master), ("ai_done", ai_done)):
        artifacts.append(
            {
                "kind": kind,
                "drive_file_id": item["file_id"],
                "name": item["name"],
                "mime_type": item["mime_type"],
                "size_bytes": int(item["size_bytes"]),
                "sha256": item["sha256"],
                "md5": item["md5"],
                "parent_folder_id": target_folder_id,
            }
        )
    manifest = {
        "schema": "universal-video-terminal-manifest-v1",
        "job_id": stable_job_key,
        "source_file_id": source_file_id,
        "source_identity": source_identity,
        "algorithm_revision": algorithm_revision,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "publication_state": "NOT_PUBLISHED",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "artifacts": artifacts,
    }
    manifest_sha256 = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    evidence_core = {
        "schema": "universal-video-terminal-receipt-v1",
        "job_id": stable_job_key,
        "source_file_id": source_file_id,
        "source_identity": source_identity,
        "source_identity_verified": True,
        "route_readback_verified": True,
        "result_readback_verified": True,
        "checksum_verified": True,
        "manifest_sha256": manifest_sha256,
        "artifact_count": len(artifacts),
        "publication_state": "NOT_PUBLISHED",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "media_execution_evidence_only": True,
    }
    evidence_sha256 = hashlib.sha256(_canonical_bytes(evidence_core)).hexdigest()
    result = {
        "manifest": manifest,
        "artifact_locators": {
            "master_pdf_drive_id": locators["master_pdf"],
            "ai_done_drive_id": locators["ai_done"],
        },
        "terminal_receipt": {**evidence_core, "evidence_sha256": evidence_sha256},
        "terminal_evidence_sha256": evidence_sha256,
    }
    validate_terminal_output(claim, result)
    return result


def validate_terminal_output(
    claim: Mapping[str, Any],
    output: Mapping[str, Any],
) -> None:
    """Validate the complete terminal contract immediately before queue finish.

    This second gate prevents supported/custom processors from transitioning a
    job to REVIEW_READY with a legacy or partial result mapping.
    """

    manifest = _mapping(output.get("manifest"), "UV_TERMINAL_MANIFEST_INVALID")
    locators = _mapping(output.get("artifact_locators"), "UV_TERMINAL_LOCATOR_INVALID")
    receipt = _mapping(output.get("terminal_receipt"), "UV_TERMINAL_RECEIPT_INVALID")
    source_identity = source_identity_from_claim(claim)
    job_id = _text(
        claim.get("stable_job_key"),
        "UV_TERMINAL_JOB_IDENTITY_MISMATCH",
        maximum=160,
    )
    algorithm_revision = _text(
        claim.get("algorithm_revision"),
        "UV_TERMINAL_JOB_IDENTITY_MISMATCH",
        maximum=80,
    )
    target_folder_id = _drive_id(claim.get("output_folder_id"))

    if set(locators) != {"master_pdf_drive_id", "ai_done_drive_id"}:
        raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_INVALID")
    master_id = _drive_id(locators.get("master_pdf_drive_id"))
    ai_done_id = _drive_id(locators.get("ai_done_drive_id"))
    if master_id == ai_done_id or source_identity["file_id"] in {master_id, ai_done_id}:
        raise TerminalEvidenceError("UV_TERMINAL_LOCATOR_INVALID")

    expected_manifest_fields = {
        "schema": "universal-video-terminal-manifest-v1",
        "job_id": job_id,
        "source_file_id": source_identity["file_id"],
        "source_identity": source_identity,
        "algorithm_revision": algorithm_revision,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "publication_state": "NOT_PUBLISHED",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
    }
    for key, expected in expected_manifest_fields.items():
        if manifest.get(key) != expected:
            raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID")

    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) != 2:
        raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID")
    artifacts: dict[str, dict[str, Any]] = {}
    for raw in raw_artifacts:
        artifact = _mapping(raw, "UV_TERMINAL_MANIFEST_INVALID")
        kind = str(artifact.get("kind") or "")
        if kind in artifacts:
            raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID")
        artifacts[kind] = artifact
    if set(artifacts) != {"master_pdf", "ai_done"}:
        raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID")

    expected_artifacts = {
        "master_pdf": (master_id, "application/pdf"),
        "ai_done": (ai_done_id, "application/json"),
    }
    for kind, (expected_id, expected_mime) in expected_artifacts.items():
        artifact = artifacts[kind]
        try:
            size_bytes = int(artifact.get("size_bytes") or 0)
        except (TypeError, ValueError) as exc:
            raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID") from exc
        if (
            _drive_id(artifact.get("drive_file_id")) != expected_id
            or _text(
                artifact.get("name"),
                "UV_TERMINAL_MANIFEST_INVALID",
            )
            != artifact.get("name")
            or artifact.get("mime_type") != expected_mime
            or size_bytes <= 0
            or artifact.get("parent_folder_id") != target_folder_id
            or not SHA256_RE.fullmatch(str(artifact.get("sha256") or ""))
            or not MD5_RE.fullmatch(str(artifact.get("md5") or ""))
        ):
            raise TerminalEvidenceError("UV_TERMINAL_MANIFEST_INVALID")

    manifest_sha256 = hashlib.sha256(_canonical_bytes(manifest)).hexdigest()
    expected_receipt_fields = {
        "schema": "universal-video-terminal-receipt-v1",
        "job_id": job_id,
        "source_file_id": source_identity["file_id"],
        "source_identity": source_identity,
        "source_identity_verified": True,
        "route_readback_verified": True,
        "result_readback_verified": True,
        "checksum_verified": True,
        "manifest_sha256": manifest_sha256,
        "artifact_count": 2,
        "publication_state": "NOT_PUBLISHED",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "media_execution_evidence_only": True,
    }
    for key, expected in expected_receipt_fields.items():
        if receipt.get(key) != expected:
            raise TerminalEvidenceError("UV_TERMINAL_RECEIPT_INVALID")
    if set(receipt) != set(expected_receipt_fields) | {"evidence_sha256"}:
        raise TerminalEvidenceError("UV_TERMINAL_RECEIPT_INVALID")
    evidence_sha256 = str(receipt.get("evidence_sha256") or "")
    if (
        not SHA256_RE.fullmatch(evidence_sha256)
        or hashlib.sha256(_canonical_bytes(expected_receipt_fields)).hexdigest() != evidence_sha256
        or output.get("terminal_evidence_sha256") != evidence_sha256
    ):
        raise TerminalEvidenceError("UV_TERMINAL_RECEIPT_INVALID")


__all__ = [
    "MAX_AI_DONE_BYTES",
    "MAX_MASTER_PDF_BYTES",
    "TerminalEvidenceError",
    "build_terminal_evidence",
    "readback_drive_bytes",
    "source_identity_from_claim",
    "validate_terminal_output",
]
