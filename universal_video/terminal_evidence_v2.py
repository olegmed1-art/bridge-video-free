"""Fail-closed two-artifact terminal evidence for Issue #881."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .drive_adapter import download_file, file_metadata

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CHECKSUM_RE = re.compile(r"^(?:md5:[0-9a-f]{32}|sha1:[0-9a-f]{40}|sha256:[0-9a-f]{64})$")


class TerminalEvidenceV2Error(RuntimeError):
    error_code = "UV_TERMINAL_EVIDENCE_V2_FAILED"

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.error_code = code


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def source_identity_from_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    try:
        size = int(claim.get("source_size_bytes"))
    except (TypeError, ValueError) as exc:
        raise TerminalEvidenceV2Error("UV_SOURCE_IDENTITY_SIZE_INVALID") from exc
    raw_checksum = claim.get("source_checksum")
    checksum = None if raw_checksum is None else str(raw_checksum).strip().lower()
    identity = {
        "file_id": str(claim.get("source_file_id") or ""),
        "name": str(claim.get("source_name") or ""),
        "mime_type": str(claim.get("source_mime_type") or ""),
        "size_bytes": size,
        "parent_folder_id": str(claim.get("source_folder_id") or ""),
        # Migration 0056 deliberately permits providers that expose no content
        # checksum. Preserve that absence as JSON null; malformed non-null
        # values still fail closed.
        "checksum": checksum,
    }
    if (
        not identity["file_id"]
        or not identity["name"]
        or not identity["mime_type"]
        or identity["size_bytes"] <= 0
        or not identity["parent_folder_id"]
        or (checksum is not None and not _CHECKSUM_RE.fullmatch(checksum))
    ):
        raise TerminalEvidenceV2Error("UV_SOURCE_IDENTITY_INVALID")
    return identity


def _metadata(meta: Mapping[str, Any]) -> dict[str, Any]:
    try:
        size = int(meta.get("size"))
    except (TypeError, ValueError) as exc:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_SIZE_INVALID") from exc
    return {
        "id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "mime_type": str(meta.get("mimeType") or ""),
        "size_bytes": size,
        "parents": [str(v) for v in (meta.get("parents") or []) if v],
        "modified_time": str(meta.get("modifiedTime") or ""),
        "version": str(meta.get("version") or ""),
        "md5_checksum": str(meta.get("md5Checksum") or "").strip().lower(),
        "sha1_checksum": str(meta.get("sha1Checksum") or "").strip().lower(),
        "sha256_checksum": str(meta.get("sha256Checksum") or "").strip().lower(),
    }


def _reject_trashed(meta: Mapping[str, Any]) -> None:
    # The real Drive adapter explicitly requests this field. A positive trash
    # state is never eligible terminal evidence, even while direct reads still
    # succeed for a recoverable trashed object.
    if meta.get("trashed") is True:
        raise TerminalEvidenceV2Error("UV_TERMINAL_ARTIFACT_TRASHED")


def _read_live(
    file_id: str,
    *,
    token: str,
    expected_parent: str,
    expected_mime: str,
    suffix: str,
    max_bytes: int,
    metadata_reader: Callable[[str, str], Mapping[str, Any]],
    downloader: Callable[..., Mapping[str, Any]],
) -> tuple[dict[str, Any], bytes]:
    try:
        before_raw = dict(metadata_reader(file_id, token))
        _reject_trashed(before_raw)
        before = _metadata(before_raw)
    except TerminalEvidenceV2Error:
        raise
    except Exception as exc:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_READ_FAILED") from exc
    if (
        before["id"] != file_id
        or not before["name"]
        or before["mime_type"] != expected_mime
        or before["size_bytes"] <= 0
        or before["parents"] != [expected_parent]
        or not before["modified_time"]
        or not before["version"]
    ):
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="uv-terminal-v2-") as td:
        destination = Path(td) / f"artifact{suffix}"
        try:
            downloaded_raw = dict(
                downloader(
                    file_id,
                    destination,
                    token,
                    max_bytes=max_bytes,
                    metadata=before_raw,
                )
            )
            _reject_trashed(downloaded_raw)
        except TerminalEvidenceV2Error:
            raise
        except Exception as exc:
            raise TerminalEvidenceV2Error("UV_TERMINAL_DRIVE_READBACK_FAILED") from exc
        downloaded = _metadata(downloaded_raw)
        actual_sha = str(downloaded_raw.get("_download_sha256") or "").strip().lower()
        if (
            downloaded != before
            or not destination.is_file()
            or destination.stat().st_size != before["size_bytes"]
            or not _SHA256_RE.fullmatch(actual_sha)
        ):
            raise TerminalEvidenceV2Error("UV_TERMINAL_READBACK_MISMATCH")
        payload = destination.read_bytes()
        if hashlib.sha256(payload).hexdigest() != actual_sha:
            raise TerminalEvidenceV2Error("UV_TERMINAL_CHECKSUM_MISMATCH")

    try:
        after_raw = dict(metadata_reader(file_id, token))
        _reject_trashed(after_raw)
        after = _metadata(after_raw)
    except TerminalEvidenceV2Error:
        raise
    except Exception as exc:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_REREAD_FAILED") from exc
    if after != before:
        raise TerminalEvidenceV2Error("UV_TERMINAL_METADATA_CHANGED")

    return {
        "drive_id": file_id,
        "name": before["name"],
        "mime_type": before["mime_type"],
        "size_bytes": before["size_bytes"],
        "parent_id": expected_parent,
        # Bind the attestation to the exact Drive revision, not only to the
        # current bytes. Drive can replace an object with identical content
        # while advancing these fields; the terminal re-verification must
        # detect that replacement.
        "modified_time": before["modified_time"],
        "version": before["version"],
        "sha256": actual_sha,
    }, payload


def _validate_done(claim: Mapping[str, Any], done: Mapping[str, Any]) -> None:
    if (
        done.get("status") != "AI_DONE"
        or str(done.get("job_id") or "") != str(claim.get("stable_job_key") or "")
        or str(done.get("algorithmRevision") or "") != str(claim.get("algorithm_revision") or "")
        or str((done.get("original") or {}).get("driveId") or "") != str(claim.get("source_file_id") or "")
    ):
        raise TerminalEvidenceV2Error("UV_AI_DONE_IDENTITY_MISMATCH")


def _build(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    master_id: str,
    ai_done_id: str,
    token: str,
    *,
    metadata_reader: Callable[[str, str], Mapping[str, Any]],
    downloader: Callable[..., Mapping[str, Any]],
) -> dict[str, Any]:
    _validate_done(claim, done)
    source = source_identity_from_claim(claim)
    output_folder = str(claim.get("output_folder_id") or "")
    if not output_folder or not master_id or not ai_done_id or len({source["file_id"], master_id, ai_done_id}) != 3:
        raise TerminalEvidenceV2Error("UV_TERMINAL_LOCATOR_INVALID")

    master, master_bytes = _read_live(
        master_id,
        token=token,
        expected_parent=output_folder,
        expected_mime="application/pdf",
        suffix=".pdf",
        max_bytes=512 * 1024 * 1024,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )
    ai, ai_bytes = _read_live(
        ai_done_id,
        token=token,
        expected_parent=output_folder,
        expected_mime="application/json",
        suffix=".json",
        max_bytes=8 * 1024 * 1024,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )
    try:
        live_done = json.loads(ai_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TerminalEvidenceV2Error("UV_AI_DONE_READBACK_INVALID") from exc
    if not isinstance(live_done, dict) or _canonical_json(live_done) != _canonical_json(dict(done)):
        raise TerminalEvidenceV2Error("UV_AI_DONE_READBACK_MISMATCH")

    expected_master_sha = str((done.get("masterPdf") or {}).get("sha256") or "").strip().lower()
    expected_master_id = str((done.get("masterPdf") or {}).get("driveId") or "")
    if expected_master_id != master_id or not _SHA256_RE.fullmatch(expected_master_sha) or expected_master_sha != master["sha256"]:
        raise TerminalEvidenceV2Error("UV_MASTER_PDF_READBACK_MISMATCH")

    artifacts = [
        {"kind": "master_pdf", "locator": f"gdrive:file:{master_id}", **master},
        {"kind": "ai_done", "locator": f"gdrive:file:{ai_done_id}", **ai},
    ]
    manifest = {
        "schema_version": "universal-video-artifact-manifest/v1",
        "job_id": str(claim.get("stable_job_key") or ""),
        "source_file_id": source["file_id"],
        "algorithm_revision": str(claim.get("algorithm_revision") or ""),
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_identity": source,
        "artifacts": artifacts,
    }
    manifest_sha = _canonical_sha256(manifest)
    receipt_core = {
        "schema_version": "universal-video-terminal-receipt/v1",
        "status": "PASS",
        "job_id": manifest["job_id"],
        "source_file_id": source["file_id"],
        "source_identity_verified": True,
        "drive_readback_verified": True,
        "result_readback_verified": True,
        "checksum_verified": True,
        "artifact_count": 2,
        "artifact_manifest_sha256": manifest_sha,
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    evidence_sha = _canonical_sha256(receipt_core)
    receipt = {**receipt_core, "evidence_sha256": evidence_sha}
    return {
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "source_file_id": source["file_id"],
        "stable_job_key": manifest["job_id"],
        "algorithm_revision": manifest["algorithm_revision"],
        "master_pdf_drive_id": master_id,
        "master_pdf_sha256": master["sha256"],
        "ai_done_drive_id": ai_done_id,
        "ai_done_sha256": ai["sha256"],
        "artifact_locators": {"master_pdf": master_id, "ai_done": ai_done_id},
        "artifact_manifest": manifest,
        "artifact_manifest_sha256": manifest_sha,
        "terminal_receipt": receipt,
        "terminal_evidence_sha256": evidence_sha,
    }


def build_terminal_evidence(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    route_receipt: Mapping[str, Any],
    token: str,
    *,
    metadata_reader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
    downloader: Callable[..., Mapping[str, Any]] = download_file,
) -> dict[str, Any]:
    if (
        route_receipt.get("schema_version") != "universal-video-route-receipt/v2"
        or str(route_receipt.get("job_id") or "") != str(claim.get("stable_job_key") or "")
        or str(route_receipt.get("source_file_id") or "") != str(claim.get("source_file_id") or "")
        or str(route_receipt.get("output_folder_id") or "") != str(claim.get("output_folder_id") or "")
    ):
        raise TerminalEvidenceV2Error("UV_ROUTE_RECEIPT_BINDING_INVALID")
    return _build(
        claim,
        done,
        str(route_receipt.get("master_pdf_drive_id") or ""),
        str(route_receipt.get("ai_done_drive_id") or ""),
        token,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )


def validate_terminal_output(claim: Mapping[str, Any], output: Mapping[str, Any]) -> None:
    required = (
        "master_pdf_drive_id", "master_pdf_sha256", "ai_done_drive_id", "ai_done_sha256",
        "artifact_locators", "artifact_manifest", "artifact_manifest_sha256",
        "terminal_receipt", "terminal_evidence_sha256",
    )
    if any(key not in output for key in required):
        raise TerminalEvidenceV2Error("UV_TERMINAL_OUTPUT_INCOMPLETE")
    manifest = output.get("artifact_manifest")
    receipt = output.get("terminal_receipt")
    locators = output.get("artifact_locators")
    if not isinstance(manifest, Mapping) or not isinstance(receipt, Mapping) or not isinstance(locators, Mapping):
        raise TerminalEvidenceV2Error("UV_TERMINAL_OUTPUT_TYPE_INVALID")
    manifest_sha = str(output.get("artifact_manifest_sha256") or "").lower()
    evidence_sha = str(output.get("terminal_evidence_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(manifest_sha) or _canonical_sha256(manifest) != manifest_sha:
        raise TerminalEvidenceV2Error("UV_TERMINAL_MANIFEST_HASH_INVALID")
    core = dict(receipt)
    receipt_evidence = str(core.pop("evidence_sha256", "") or "").lower()
    if not _SHA256_RE.fullmatch(evidence_sha) or receipt_evidence != evidence_sha or _canonical_sha256(core) != evidence_sha:
        raise TerminalEvidenceV2Error("UV_TERMINAL_RECEIPT_HASH_INVALID")
    source = source_identity_from_claim(claim)
    if (
        output.get("result_mode") != "SHADOW_REVIEW_ONLY"
        or output.get("canonical_promotion_allowed") is not False
        or output.get("database_persistence_allowed") is not False
        or output.get("publication_state") != "NOT_PUBLISHED"
        or output.get("source_file_id") != claim.get("source_file_id")
        or output.get("stable_job_key") != claim.get("stable_job_key")
        or output.get("algorithm_revision") != claim.get("algorithm_revision")
        or manifest.get("source_identity") != source
        or manifest.get("job_id") != claim.get("stable_job_key")
        or manifest.get("source_file_id") != claim.get("source_file_id")
        or manifest.get("algorithm_revision") != claim.get("algorithm_revision")
        or receipt.get("status") != "PASS"
        or receipt.get("source_identity_verified") is not True
        or receipt.get("drive_readback_verified") is not True
        or receipt.get("result_readback_verified") is not True
        or receipt.get("checksum_verified") is not True
        or receipt.get("artifact_count") != 2
        or receipt.get("artifact_manifest_sha256") != manifest_sha
        or locators.get("master_pdf") != output.get("master_pdf_drive_id")
        or locators.get("ai_done") != output.get("ai_done_drive_id")
    ):
        raise TerminalEvidenceV2Error("UV_TERMINAL_OUTPUT_BINDING_INVALID")


def reverify_terminal_output_live(
    claim: Mapping[str, Any],
    output: Mapping[str, Any],
    token: str,
    *,
    metadata_reader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
    downloader: Callable[..., Mapping[str, Any]] = download_file,
) -> dict[str, Any]:
    validate_terminal_output(claim, output)
    ai_id = str(output.get("ai_done_drive_id") or "")
    try:
        ai_raw = dict(metadata_reader(ai_id, token))
        _reject_trashed(ai_raw)
        with tempfile.TemporaryDirectory(prefix="uv-terminal-ai-v2-") as td:
            path = Path(td) / "AI_DONE.json"
            downloader(ai_id, path, token, max_bytes=8 * 1024 * 1024, metadata=ai_raw)
            done = json.loads(path.read_text(encoding="utf-8-sig"))
    except TerminalEvidenceV2Error:
        raise
    except Exception as exc:
        raise TerminalEvidenceV2Error("UV_AI_DONE_REVERIFY_FAILED") from exc
    if not isinstance(done, dict):
        raise TerminalEvidenceV2Error("UV_AI_DONE_REVERIFY_INVALID")
    rebuilt = _build(
        claim,
        done,
        str(output.get("master_pdf_drive_id") or ""),
        ai_id,
        token,
        metadata_reader=metadata_reader,
        downloader=downloader,
    )
    for key in (
        "master_pdf_drive_id", "master_pdf_sha256", "ai_done_drive_id", "ai_done_sha256",
        "artifact_locators", "artifact_manifest", "artifact_manifest_sha256",
        "terminal_receipt", "terminal_evidence_sha256",
    ):
        if output.get(key) != rebuilt.get(key):
            raise TerminalEvidenceV2Error("UV_TERMINAL_LIVE_EVIDENCE_MISMATCH")
    return rebuilt


__all__ = [
    "TerminalEvidenceV2Error",
    "build_terminal_evidence",
    "reverify_terminal_output_live",
    "source_identity_from_claim",
    "validate_terminal_output",
]
