"""Read-only pre-canary attestations. This module never downloads source media."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import re
from typing import Any, Mapping

from .drive_adapter import access_token, file_metadata
from .result_contract import synthetic_result_contract_self_test

IMPORT_CLOSURE = (
    "universal_video.neon_worker",
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


def attest_synthetic_contract() -> dict[str, Any]:
    result = synthetic_result_contract_self_test()
    receipt = {
        "status": "PASS",
        "gate": "SYNTHETIC_RESULT_CONTRACT",
        "drive_readback_verified": result["terminal_receipt"]["drive_readback_verified"],
        "artifact_manifest_sha256": result["artifact_manifest_sha256"],
        "drive_write_performed": False,
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
