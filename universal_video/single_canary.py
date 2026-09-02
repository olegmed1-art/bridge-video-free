"""Prepare and enqueue exactly one hash-bound Universal Video canary."""
from __future__ import annotations

import argparse
import json
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from .drive_adapter import access_token, file_metadata
from .source_identity import (
    SourceIdentityError,
    normalize_expected_identity,
    read_source_identity,
    verify_expected_source_identity,
)
from .video_queue import VideoQueueError, build_drive_manifest, enqueue_manifest

EXACT_CANARY_PROFILE = "bridge_3_1_free_exact_canary"
EXACT_CANARY_REVISION = "3.1-free-r25.16"
SCHEMA = "universal-video-exact-single-canary-request-v1"
MAX_REQUEST_BYTES = 32 * 1024
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUEST_FIELDS = frozenset({
    "schema",
    "request_key",
    "runtime_sha",
    "image_digest",
    "source",
    "output_folder_id",
    "work_folder_id",
    "processing_profile",
    "algorithm_revision",
    "canonical_promotion_allowed",
    "publication_state",
})


class ExactSingleCanaryError(RuntimeError):
    error_code = "UV_EXACT_SINGLE_CANARY_FAILED"


def _text(value: Any, field: str, *, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise ExactSingleCanaryError(f"INVALID_{field.upper()}")
    return result


def _folder(folder_id: str, token: str, field: str) -> None:
    metadata = file_metadata(folder_id, token)
    if (
        str(metadata.get("id") or "") != folder_id
        or metadata.get("mimeType") != "application/vnd.google-apps.folder"
    ):
        raise ExactSingleCanaryError(f"{field.upper()}_INVALID")


def _source_without_checksum(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        size_bytes = int(value.get("size_bytes") or 0)
    except (TypeError, ValueError) as exc:
        raise ExactSingleCanaryError("SOURCE_SIZE_INVALID") from exc
    result = {
        "file_id": _text(value.get("file_id"), "source_file_id", maximum=200),
        "name": _text(value.get("name"), "source_name"),
        "mime_type": _text(value.get("mime_type"), "source_mime_type", maximum=200),
        "size_bytes": size_bytes,
        "parent_id": _text(value.get("parent_id"), "source_parent_id", maximum=200),
    }
    if not result["mime_type"].startswith("video/") or result["size_bytes"] <= 0:
        raise ExactSingleCanaryError("SOURCE_IDENTITY_INVALID")
    return result


def prepare_exact_request(
    *,
    request_key: str,
    runtime_sha: str,
    image_digest: str,
    expected_source: Mapping[str, Any],
    output_folder_id: str,
    work_folder_id: str,
    token: str,
) -> dict[str, Any]:
    """Capture the provider checksum without reading source media bytes."""

    expected = _source_without_checksum(expected_source)
    observed = read_source_identity(expected["file_id"], token)
    if {key: observed[key] for key in expected} != expected:
        raise ExactSingleCanaryError("SOURCE_METADATA_PREFLIGHT_MISMATCH")
    output = _text(output_folder_id, "output_folder_id", maximum=200)
    work = _text(work_folder_id, "work_folder_id", maximum=200)
    if observed["parent_id"] in {output, work} or output == work:
        raise ExactSingleCanaryError("CANARY_FOLDER_ISOLATION_INVALID")
    _folder(output, token, "output_folder_id")
    _folder(work, token, "work_folder_id")
    request = {
        "schema": SCHEMA,
        "request_key": _text(request_key, "request_key", maximum=160),
        "runtime_sha": runtime_sha.strip().lower(),
        "image_digest": image_digest.strip().lower(),
        "source": observed,
        "output_folder_id": output,
        "work_folder_id": work,
        "processing_profile": EXACT_CANARY_PROFILE,
        "algorithm_revision": EXACT_CANARY_REVISION,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }
    return validate_exact_request(request)


def validate_exact_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _REQUEST_FIELDS:
        raise ExactSingleCanaryError("EXACT_CANARY_REQUEST_FIELDS_INVALID")
    runtime_sha = str(payload.get("runtime_sha") or "").strip().lower()
    image_digest = str(payload.get("image_digest") or "").strip().lower()
    if not _COMMIT_RE.fullmatch(runtime_sha) or not _IMAGE_RE.fullmatch(image_digest):
        raise ExactSingleCanaryError("EXACT_RUNTIME_IMAGE_IDENTITY_INVALID")
    if payload.get("processing_profile") != EXACT_CANARY_PROFILE:
        raise ExactSingleCanaryError("EXACT_CANARY_PROFILE_INVALID")
    if payload.get("algorithm_revision") != EXACT_CANARY_REVISION:
        raise ExactSingleCanaryError("EXACT_CANARY_REVISION_INVALID")
    if payload.get("canonical_promotion_allowed") is not False:
        raise ExactSingleCanaryError("CANONICAL_PROMOTION_MUST_BE_FALSE")
    if payload.get("publication_state") != "NOT_PUBLISHED":
        raise ExactSingleCanaryError("PUBLICATION_STATE_INVALID")
    source = normalize_expected_identity(payload.get("source") or {})
    output = _text(payload.get("output_folder_id"), "output_folder_id", maximum=200)
    work = _text(payload.get("work_folder_id"), "work_folder_id", maximum=200)
    if source["parent_id"] in {output, work} or output == work:
        raise ExactSingleCanaryError("CANARY_FOLDER_ISOLATION_INVALID")
    return {
        "schema": SCHEMA,
        "request_key": _text(payload.get("request_key"), "request_key", maximum=160),
        "runtime_sha": runtime_sha,
        "image_digest": image_digest,
        "source": source,
        "output_folder_id": output,
        "work_folder_id": work,
        "processing_profile": EXACT_CANARY_PROFILE,
        "algorithm_revision": EXACT_CANARY_REVISION,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }


def _raw_inventory_item(identity: Mapping[str, Any]) -> dict[str, Any]:
    algorithm, checksum = str(identity["checksum"]).split(":", 1)
    checksum_field = {
        "sha256": "sha256Checksum",
        "sha1": "sha1Checksum",
        "md5": "md5Checksum",
    }[algorithm]
    return {
        "id": identity["file_id"],
        "name": identity["name"],
        "mimeType": identity["mime_type"],
        "size": identity["size_bytes"],
        "parents": [identity["parent_id"]],
        checksum_field: checksum,
    }


def enqueue_exact_single_canary(
    request: Mapping[str, Any],
    *,
    database_url: str,
    token: str,
) -> dict[str, Any]:
    """Re-read source identity and atomically register a one-item batch only."""

    exact = validate_exact_request(request)
    observed = verify_expected_source_identity(exact["source"], token)
    _folder(exact["output_folder_id"], token, "output_folder_id")
    _folder(exact["work_folder_id"], token, "work_folder_id")
    base_request = {
        "request_key": exact["request_key"],
        "source_folder_id": observed["parent_id"],
        "output_folder_id": exact["output_folder_id"],
        "work_folder_id": exact["work_folder_id"],
        "processing_profile": EXACT_CANARY_PROFILE,
        "algorithm_revision": EXACT_CANARY_REVISION,
        "canary_source_file_id": observed["file_id"],
    }
    # The only inventory item is the exact, just-re-read source. No source
    # folder enumeration occurs, so no second video can be released later.
    manifest = build_drive_manifest(base_request, [_raw_inventory_item(observed)])
    if (
        manifest.get("expected_count") != 1
        or len(manifest.get("files") or []) != 1
        or manifest["files"][0].get("file_id") != observed["file_id"]
    ):
        raise ExactSingleCanaryError("EXACTLY_ONE_MANIFEST_INVARIANT_FAILED")
    receipt = enqueue_manifest(manifest, database_url)
    if (
        int(receipt.get("expected_count") or 0) != 1
        or receipt.get("canary_source_file_id") != observed["file_id"]
        or receipt.get("canonical_promotion_allowed") is not False
    ):
        raise ExactSingleCanaryError("EXACTLY_ONE_ENQUEUE_RECEIPT_INVALID")
    return {
        "schema": "universal-video-exact-single-canary-enqueue-v1",
        "status": "QUEUED_EXACTLY_ONE",
        "runtime_sha": exact["runtime_sha"],
        "image_digest": exact["image_digest"],
        "source_identity": observed,
        "source_identity_gate": "PASS",
        "expected_count": 1,
        "batch_receipt": receipt,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "publication_state": "NOT_PUBLISHED",
    }


def _read_request(path: Path) -> dict[str, Any]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExactSingleCanaryError("REQUEST_MUST_BE_REGULAR_FILE")
    if info.st_size > MAX_REQUEST_BYTES:
        raise ExactSingleCanaryError("REQUEST_TOO_LARGE")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExactSingleCanaryError("REQUEST_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ExactSingleCanaryError("REQUEST_JSON_NOT_OBJECT")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    probe = commands.add_parser("probe")
    probe.add_argument("--request-key", required=True)
    probe.add_argument("--runtime-sha", required=True)
    probe.add_argument("--image-digest", required=True)
    probe.add_argument("--source-file-id", required=True)
    probe.add_argument("--source-name", required=True)
    probe.add_argument("--source-mime-type", required=True)
    probe.add_argument("--source-size-bytes", required=True, type=int)
    probe.add_argument("--source-parent-id", required=True)
    probe.add_argument("--output-folder-id", required=True)
    probe.add_argument("--work-folder-id", required=True)
    enqueue = commands.add_parser("enqueue")
    enqueue.add_argument("request", type=Path)
    args = parser.parse_args()
    try:
        token = access_token()
        if args.command == "probe":
            result = prepare_exact_request(
                request_key=args.request_key,
                runtime_sha=args.runtime_sha,
                image_digest=args.image_digest,
                expected_source={
                    "file_id": args.source_file_id,
                    "name": args.source_name,
                    "mime_type": args.source_mime_type,
                    "size_bytes": args.source_size_bytes,
                    "parent_id": args.source_parent_id,
                },
                output_folder_id=args.output_folder_id,
                work_folder_id=args.work_folder_id,
                token=token,
            )
        else:
            from .video_queue import database_url_from_env

            result = enqueue_exact_single_canary(
                _read_request(args.request),
                database_url=database_url_from_env(),
                token=token,
            )
    except (ExactSingleCanaryError, SourceIdentityError, VideoQueueError) as exc:
        print(json.dumps({
            "schema": "universal-video-exact-single-canary-error-v1",
            "status": "BLOCKED",
            "error_code": getattr(exc, "error_code", "UV_EXACT_SINGLE_CANARY_FAILED"),
            "error_type": type(exc).__name__,
        }, sort_keys=True))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
