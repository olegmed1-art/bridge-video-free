"""Fail-closed Drive result verification for Universal Video canaries."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping

from .drive_adapter import download_file, file_metadata

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ResultContractError(RuntimeError):
    """A stable error that must never be converted into a successful receipt."""

    def __init__(self, error_code: str, detail: str = "") -> None:
        super().__init__(error_code if not detail else f"{error_code}: {detail}")
        self.error_code = error_code


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    packed = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()


def _metadata_identity(meta: Mapping[str, Any]) -> dict[str, Any]:
    try:
        size = int(meta.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise ResultContractError("UV_RESULT_METADATA_INVALID", "size") from exc
    parents = [str(item) for item in (meta.get("parents") or [])]
    return {
        "id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "mime_type": str(meta.get("mimeType") or ""),
        "size_bytes": size,
        "parents": parents,
        "md5": str(meta.get("md5Checksum") or "").strip().lower(),
        "sha1": str(meta.get("sha1Checksum") or "").strip().lower(),
        "sha256": str(meta.get("sha256Checksum") or "").strip().lower(),
    }


def verify_drive_result_contract(
    claim: Mapping[str, Any],
    done: Mapping[str, Any],
    *,
    token: str,
    metadata_reader: Callable[[str, str], Mapping[str, Any]] = file_metadata,
    downloader: Callable[..., Mapping[str, Any]] = download_file,
) -> dict[str, Any]:
    """Read an uploaded result back and produce a PASS receipt only after verification."""

    master = done.get("masterPdf")
    if not isinstance(master, Mapping):
        raise ResultContractError("UV_RESULT_MASTER_PDF_MISSING")
    drive_id = str(master.get("driveId") or "")
    expected_sha256 = str(master.get("sha256") or "").strip().lower()
    expected_parent = str(claim.get("output_folder_id") or "")
    source_id = str(claim.get("source_file_id") or "")
    if (
        not drive_id
        or drive_id == source_id
        or not expected_parent
        or not _SHA256_RE.fullmatch(expected_sha256)
    ):
        raise ResultContractError("UV_RESULT_LOCATOR_INVALID")

    try:
        before_raw = metadata_reader(drive_id, token)
        before = _metadata_identity(before_raw)
    except ResultContractError:
        raise
    except Exception as exc:
        raise ResultContractError("UV_RESULT_METADATA_READBACK_FAILED") from exc

    if (
        before["id"] != drive_id
        or before["mime_type"] != "application/pdf"
        or not before["name"]
        or before["size_bytes"] <= 0
        or before["parents"] != [expected_parent]
    ):
        raise ResultContractError("UV_RESULT_METADATA_MISMATCH")

    with tempfile.TemporaryDirectory(prefix="uv-result-readback-") as temp_dir:
        destination = Path(temp_dir) / "master.pdf"
        try:
            downloaded_raw = downloader(
                drive_id,
                destination,
                token,
                max_bytes=512 * 1024 * 1024,
                metadata=dict(before_raw),
            )
        except Exception as exc:
            raise ResultContractError("UV_RESULT_DRIVE_READBACK_FAILED") from exc
        downloaded = _metadata_identity(downloaded_raw)
        actual_sha256 = str(downloaded_raw.get("_download_sha256") or "").strip().lower()
        if not destination.is_file() or destination.stat().st_size != before["size_bytes"]:
            raise ResultContractError("UV_RESULT_READBACK_SIZE_MISMATCH")
        if not _SHA256_RE.fullmatch(actual_sha256) or actual_sha256 != expected_sha256:
            raise ResultContractError("UV_RESULT_CHECKSUM_MISMATCH")
        if downloaded != before:
            raise ResultContractError("UV_RESULT_DOWNLOAD_METADATA_MISMATCH")

    try:
        after = _metadata_identity(metadata_reader(drive_id, token))
    except ResultContractError:
        raise
    except Exception as exc:
        raise ResultContractError("UV_RESULT_METADATA_REREAD_FAILED") from exc
    if after != before:
        raise ResultContractError("UV_RESULT_METADATA_CHANGED_DURING_READBACK")

    artifact_manifest = {
        "schema_version": "universal-video-artifact-manifest/v1",
        "job_id": str(claim.get("stable_job_key") or ""),
        "source_file_id": source_id,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "artifacts": [
            {
                "kind": "master_pdf",
                "locator": f"gdrive:file:{drive_id}",
                "drive_id": drive_id,
                "name": before["name"],
                "mime_type": before["mime_type"],
                "size_bytes": before["size_bytes"],
                "parent_id": expected_parent,
                "sha256": actual_sha256,
            }
        ],
    }
    manifest_sha256 = _canonical_sha256(artifact_manifest)
    terminal_receipt = {
        "schema_version": "universal-video-terminal-receipt/v1",
        "status": "PASS",
        "job_id": str(claim.get("stable_job_key") or ""),
        "source_file_id": source_id,
        "drive_readback_verified": True,
        "artifact_manifest_sha256": manifest_sha256,
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    return {
        "master_pdf_drive_id": drive_id,
        "master_pdf_sha256": actual_sha256,
        "artifact_manifest": artifact_manifest,
        "artifact_manifest_sha256": manifest_sha256,
        "terminal_receipt": terminal_receipt,
    }


def synthetic_result_contract_self_test() -> dict[str, Any]:
    """Exercise the full verifier without Drive writes or real media."""

    payload = b"%PDF-1.7\n% synthetic issue-881 readback evidence\n"
    digest = hashlib.sha256(payload).hexdigest()
    metadata = {
        "id": "synthetic-result-file-id",
        "name": "synthetic-result.pdf",
        "mimeType": "application/pdf",
        "size": str(len(payload)),
        "parents": ["synthetic-output-folder"],
        "md5Checksum": hashlib.md5(payload, usedforsecurity=False).hexdigest(),
    }

    def read_meta(file_id: str, _token: str) -> Mapping[str, Any]:
        if file_id != metadata["id"]:
            raise RuntimeError("unexpected synthetic file")
        return dict(metadata)

    def read_bytes(
        file_id: str,
        destination: Path,
        _token: str,
        **_: Any,
    ) -> Mapping[str, Any]:
        if file_id != metadata["id"]:
            raise RuntimeError("unexpected synthetic file")
        destination.write_bytes(payload)
        result = dict(metadata)
        result["_download_sha256"] = digest
        result["_download_md5"] = metadata["md5Checksum"]
        return result

    result = verify_drive_result_contract(
        {
            "stable_job_key": "0" * 32,
            "source_file_id": "synthetic-source-file-id",
            "output_folder_id": "synthetic-output-folder",
        },
        {"masterPdf": {"driveId": metadata["id"], "sha256": digest}},
        token="synthetic-no-network",
        metadata_reader=read_meta,
        downloader=read_bytes,
    )
    if result["terminal_receipt"]["status"] != "PASS":
        raise ResultContractError("UV_SYNTHETIC_RESULT_CONTRACT_FAILED")
    return result


__all__ = [
    "ResultContractError",
    "synthetic_result_contract_self_test",
    "verify_drive_result_contract",
]
