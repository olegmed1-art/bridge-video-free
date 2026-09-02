"""Read-only pre-canary attestations. This module never downloads source media."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .drive_adapter import access_token, file_metadata
from .terminal_evidence_v2 import build_terminal_evidence, validate_terminal_output

IMPORT_CLOSURE = (
    "universal_video.neon_worker",
    "universal_video.route_receipt_v2",
    "universal_video.terminal_evidence_v2",
    "bridge_worker_3_1_free",
    "bridge_runtime_hardening_r25_16",
    "route_drive_job_outputs",
    "bridge_vision",
    "bridge_contracts",
    "psycopg",
)


def _checksum(meta: Mapping[str, Any]) -> tuple[str, str]:
    for field, kind, length in (
        ("sha256Checksum", "sha256", 64),
        ("sha1Checksum", "sha1", 40),
        ("md5Checksum", "md5", 32),
    ):
        value = str(meta.get(field) or "").strip().lower()
        if value:
            if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
                raise RuntimeError("UV_SOURCE_IDENTITY_CHECKSUM_INVALID")
            return kind, value
    raise RuntimeError("UV_SOURCE_IDENTITY_CHECKSUM_MISSING")


def attest_imports() -> dict[str, Any]:
    loaded = []
    for module_name in IMPORT_CLOSURE:
        importlib.import_module(module_name)
        loaded.append(module_name)
    receipt = {
        "status": "PASS",
        "gate": "IMPORT_CLOSURE",
        "modules": loaded,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return receipt


def attest_source_identity(args: argparse.Namespace) -> dict[str, Any]:
    token = access_token()
    meta = file_metadata(args.file_id, token)
    kind, value = _checksum(meta)
    observed = {
        "file_id": str(meta.get("id") or ""),
        "name": str(meta.get("name") or ""),
        "mime_type": str(meta.get("mimeType") or ""),
        "size_bytes": int(meta.get("size") or 0),
        "parents": [str(item) for item in (meta.get("parents") or [])],
        "checksum_type": kind,
        "checksum_value": value,
    }
    expected = {
        "file_id": args.file_id,
        "name": args.name,
        "mime_type": args.mime_type,
        "size_bytes": args.size,
        "parents": [args.parent],
    }
    if {key: observed[key] for key in expected} != expected:
        raise RuntimeError("UV_SOURCE_IDENTITY_MISMATCH")
    identity_sha256 = hashlib.sha256(
        json.dumps(observed, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    receipt = {
        "status": "PASS",
        "gate": "SOURCE_IDENTITY_METADATA_ONLY",
        **observed,
        "identity_sha256": identity_sha256,
        "source_media_downloaded": False,
        "video_job_submitted": False,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return receipt


def _synthetic_terminal_v2() -> dict[str, Any]:
    master_bytes = b"%PDF-1.7\n% issue-881 terminal-v2 synthetic\n"
    master_sha = hashlib.sha256(master_bytes).hexdigest()
    claim = {
        "stable_job_key": "1" * 32,
        "source_file_id": "synthetic-source-123456",
        "source_name": "synthetic-source.mp4",
        "source_mime_type": "video/mp4",
        "source_size_bytes": 12345678,
        "source_folder_id": "synthetic-source-folder",
        "source_checksum": "md5:" + "1" * 32,
        "output_folder_id": "synthetic-output-folder",
        "algorithm_revision": "3.1-free-r25.16",
    }
    master_id = "synthetic-master-pdf-123456"
    ai_done_id = "synthetic-ai-done-123456"
    done = {
        "status": "AI_DONE",
        "job_id": claim["stable_job_key"],
        "algorithmRevision": claim["algorithm_revision"],
        "original": {"driveId": claim["source_file_id"]},
        "masterPdf": {"driveId": master_id, "sha256": master_sha},
    }
    ai_bytes = json.dumps(done, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ai_sha = hashlib.sha256(ai_bytes).hexdigest()
    metadata = {
        master_id: {
            "id": master_id,
            "name": "synthetic-master.pdf",
            "mimeType": "application/pdf",
            "size": str(len(master_bytes)),
            "parents": [claim["output_folder_id"]],
        },
        ai_done_id: {
            "id": ai_done_id,
            "name": f"AI_DONE_{claim['stable_job_key']}.json",
            "mimeType": "application/json",
            "size": str(len(ai_bytes)),
            "parents": [claim["output_folder_id"]],
        },
    }
    payloads = {master_id: master_bytes, ai_done_id: ai_bytes}

    def read_meta(file_id: str, _token: str) -> Mapping[str, Any]:
        if file_id not in metadata:
            raise RuntimeError("unexpected synthetic file")
        return dict(metadata[file_id])

    def read_bytes(
        file_id: str,
        destination: Path,
        _token: str,
        **_: Any,
    ) -> Mapping[str, Any]:
        if file_id not in metadata:
            raise RuntimeError("unexpected synthetic file")
        payload = payloads[file_id]
        destination.write_bytes(payload)
        result = dict(metadata[file_id])
        result["_download_sha256"] = hashlib.sha256(payload).hexdigest()
        return result

    route_receipt = {
        "schema_version": "universal-video-route-receipt/v2",
        "job_id": claim["stable_job_key"],
        "source_file_id": claim["source_file_id"],
        "output_folder_id": claim["output_folder_id"],
        "master_pdf_drive_id": master_id,
        "ai_done_drive_id": ai_done_id,
    }
    result = build_terminal_evidence(
        claim,
        done,
        route_receipt,
        "synthetic-no-network",
        metadata_reader=read_meta,
        downloader=read_bytes,
    )
    validate_terminal_output(claim, result)
    if (
        result["terminal_receipt"].get("status") != "PASS"
        or result["terminal_receipt"].get("artifact_count") != 2
        or result["artifact_manifest"]["artifacts"][0].get("kind") != "master_pdf"
        or result["artifact_manifest"]["artifacts"][1].get("kind") != "ai_done"
        or result["ai_done_sha256"] != ai_sha
    ):
        raise RuntimeError("UV_SYNTHETIC_TERMINAL_V2_FAILED")
    return result


def attest_synthetic_contract() -> dict[str, Any]:
    result = _synthetic_terminal_v2()
    receipt = {
        "status": "PASS",
        "gate": "SYNTHETIC_RESULT_CONTRACT_V2",
        "drive_readback_verified": result["terminal_receipt"]["drive_readback_verified"],
        "source_identity_verified": result["terminal_receipt"]["source_identity_verified"],
        "artifact_count": result["terminal_receipt"]["artifact_count"],
        "master_pdf_verified": result["artifact_manifest"]["artifacts"][0]["kind"] == "master_pdf",
        "ai_done_verified": result["artifact_manifest"]["artifacts"][1]["kind"] == "ai_done",
        "artifact_manifest_sha256": result["artifact_manifest_sha256"],
        "terminal_evidence_sha256": result["terminal_evidence_sha256"],
        "drive_write_performed": False,
        "source_media_downloaded": False,
        "video_job_submitted": False,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("imports")
    sub.add_parser("synthetic-result-contract")
    source = sub.add_parser("source-identity")
    source.add_argument("--file-id", required=True)
    source.add_argument("--name", required=True)
    source.add_argument("--mime-type", required=True)
    source.add_argument("--size", type=int, required=True)
    source.add_argument("--parent", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "imports":
        attest_imports()
    elif args.command == "synthetic-result-contract":
        attest_synthetic_contract()
    else:
        attest_source_identity(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
