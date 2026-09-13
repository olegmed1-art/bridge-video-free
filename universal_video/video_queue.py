"""Neon control plane for project-neutral Google Drive video batches.

Only bounded metadata enters Postgres. Video bytes always travel directly
between Google Drive and the resident Oracle worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable, Mapping

from .drive_adapter import access_token, file_metadata, list_folder_files

DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
REQUEST_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
PROFILE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
REVISION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
MAX_FILES = 1000
MIN_FILE_BYTES = 1024**2
MAX_FILE_BYTES = 64 * 1024**3
INTAKE_FIELDS = frozenset({
    "request_key",
    "source_folder_id",
    "output_folder_id",
    "work_folder_id",
    "processing_profile",
    "algorithm_revision",
    "canary_source_file_id",
})


class VideoQueueError(ValueError):
    pass


def _required_text(value: Any, field: str, *, maximum: int = 500) -> str:
    result = str(value or "").strip()
    if not result or len(result) > maximum:
        raise VideoQueueError(f"invalid {field}")
    return result


def _drive_id(value: Any, field: str) -> str:
    result = _required_text(value, field, maximum=200)
    if not DRIVE_ID_RE.fullmatch(result):
        raise VideoQueueError(f"invalid {field}")
    return result


def validate_intake_request(payload: Any) -> dict[str, str]:
    if not isinstance(payload, Mapping) or set(payload) != INTAKE_FIELDS:
        raise VideoQueueError("intake fields must match the bounded contract")
    request_key = _required_text(payload.get("request_key"), "request_key", maximum=160)
    if not REQUEST_KEY_RE.fullmatch(request_key):
        raise VideoQueueError("invalid request_key")
    source = _drive_id(payload.get("source_folder_id"), "source_folder_id")
    output = _drive_id(payload.get("output_folder_id"), "output_folder_id")
    work = _drive_id(payload.get("work_folder_id"), "work_folder_id")
    if source in {output, work}:
        raise VideoQueueError("source folder must be isolated from result folders")
    profile = _required_text(payload.get("processing_profile"), "processing_profile", maximum=80).lower()
    revision = _required_text(payload.get("algorithm_revision"), "algorithm_revision", maximum=80)
    if not PROFILE_RE.fullmatch(profile) or not REVISION_RE.fullmatch(revision):
        raise VideoQueueError("invalid profile or algorithm revision")
    canary = _drive_id(payload.get("canary_source_file_id"), "canary_source_file_id")
    return {
        "request_key": request_key,
        "source_folder_id": source,
        "output_folder_id": output,
        "work_folder_id": work,
        "processing_profile": profile,
        "algorithm_revision": revision,
        "canary_source_file_id": canary,
    }


def _natural_key(name: str) -> tuple:
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", name)
    )


def _checksum(item: Mapping[str, Any]) -> str | None:
    for key, label, length in (
        ("sha256Checksum", "sha256", 64),
        ("sha1Checksum", "sha1", 40),
        ("md5Checksum", "md5", 32),
    ):
        value = str(item.get(key) or "").strip().lower()
        if value:
            if not re.fullmatch(rf"[0-9a-f]{{{length}}}", value):
                raise VideoQueueError("invalid Drive checksum metadata")
            return f"{label}:{value}"
    return None


def normalize_drive_inventory(folder_id: str, raw_items: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    folder_id = _drive_id(folder_id, "folder_id")
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_items:
        mime = str(raw.get("mimeType") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not mime.startswith("video/") or name.startswith("AI_PART_"):
            continue
        file_id = _drive_id(raw.get("id"), "Drive file id")
        if file_id in seen:
            raise VideoQueueError("duplicate Drive file id")
        seen.add(file_id)
        parents = [str(value) for value in (raw.get("parents") or [])]
        if parents != [folder_id]:
            raise VideoQueueError("Drive file is not an exact direct child of the source folder")
        if not name or len(name) > 500:
            raise VideoQueueError("invalid Drive file name")
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError) as exc:
            raise VideoQueueError("invalid Drive file size") from exc
        if not MIN_FILE_BYTES <= size <= MAX_FILE_BYTES:
            raise VideoQueueError("Drive video size outside bounded range")
        candidates.append({
            "file_id": file_id,
            "name": name,
            "mime_type": mime,
            "size_bytes": size,
            "checksum": _checksum(raw),
        })
    candidates.sort(key=lambda item: (_natural_key(item["name"]), item["file_id"]))
    if not 1 <= len(candidates) <= MAX_FILES:
        raise VideoQueueError("video count outside bounded range")
    return [dict(item, sequence=index) for index, item in enumerate(candidates, start=1)]


def inventory_sha256(files: list[dict[str, Any]]) -> str:
    raw = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_drive_manifest(request: Mapping[str, Any], raw_items: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    validated = validate_intake_request(request)
    files = normalize_drive_inventory(validated["source_folder_id"], raw_items)
    if validated["canary_source_file_id"] not in {item["file_id"] for item in files}:
        raise VideoQueueError("canary is absent from the normalized video inventory")
    return {
        **validated,
        "inventory_sha256": inventory_sha256(files),
        "expected_count": len(files),
        "total_size_bytes": sum(item["size_bytes"] for item in files),
        "files": files,
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
    }


def _folder(token: str, folder_id: str, field: str) -> None:
    meta = file_metadata(folder_id, token)
    if meta.get("mimeType") != "application/vnd.google-apps.folder":
        raise VideoQueueError(f"{field} is not a Google Drive folder")


def discover_drive_manifest(request: Mapping[str, Any], token: str | None = None) -> dict[str, Any]:
    validated = validate_intake_request(request)
    drive_token = token or access_token()
    _folder(drive_token, validated["output_folder_id"], "output_folder_id")
    if validated["work_folder_id"] != validated["output_folder_id"]:
        _folder(drive_token, validated["work_folder_id"], "work_folder_id")
    items = list_folder_files(validated["source_folder_id"], drive_token)
    return build_drive_manifest(validated, items)


def _read_dsn_file(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        raise VideoQueueError("video queue DSN file must be absolute")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VideoQueueError("cannot read video queue DSN file") from exc
    if not value:
        raise VideoQueueError("video queue DSN file is empty")
    return value


def database_url_from_env() -> str:
    direct = os.getenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL", "").strip()
    if direct:
        return direct
    file_name = os.getenv("BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE", "").strip()
    if file_name:
        return _read_dsn_file(file_name)
    for name in ("BRIDGE_APP_DATABASE_URL", "BRIDGE_WORKER_DATABASE_URL"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    raise VideoQueueError("video queue database credential is unavailable")


def enqueue_manifest(manifest: Mapping[str, Any], database_url: str) -> dict[str, Any]:
    from psycopg import connect
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    started = time.monotonic()
    with connect(database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM video_queue.enqueue_drive_batch(
                %s,%s,%s,%s,%s,%s,%s,%s,%s
            )
            """,
            (
                manifest["request_key"],
                manifest["source_folder_id"],
                manifest["output_folder_id"],
                manifest["work_folder_id"],
                manifest["processing_profile"],
                manifest["algorithm_revision"],
                manifest["canary_source_file_id"],
                manifest["inventory_sha256"],
                Jsonb(manifest["files"]),
            ),
        )
        row = cursor.fetchone()
    if not row:
        raise VideoQueueError("Neon did not return a batch receipt")
    return {
        "schema": "universal-video-batch-intake-v1",
        "batch_id": str(row["batch_id"]),
        "request_key": manifest["request_key"],
        "status": row["status"],
        "expected_count": int(row["expected_count"]),
        "total_size_bytes": int(row["total_size_bytes"]),
        "inventory_sha256": manifest["inventory_sha256"],
        "canary_source_file_id": manifest["canary_source_file_id"],
        "result_mode": "SHADOW_REVIEW_ONLY",
        "canonical_promotion_allowed": False,
        "database_persistence_allowed": False,
        "enqueue_elapsed_seconds": round(time.monotonic() - started, 3),
    }


def enqueue_drive_request(
    request: Mapping[str, Any],
    *,
    database_url: str | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    manifest = discover_drive_manifest(request, token)
    return enqueue_manifest(manifest, database_url or database_url_from_env())


def batch_status(request_key: str, database_url: str | None = None) -> dict[str, Any] | None:
    from psycopg import connect
    from psycopg.rows import dict_row

    if not REQUEST_KEY_RE.fullmatch(str(request_key or "")):
        raise VideoQueueError("invalid request_key")
    with connect(database_url or database_url_from_env(), row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT batch_id, request_key, processing_profile, algorithm_revision,
                   result_mode, inventory_sha256, expected_count, total_size_bytes,
                   status, pending_canary, queued, running, review_ready,
                   ambiguous, failed, canonical_promotion_allowed,
                   database_persistence_allowed, created_at, updated_at, completed_at
              FROM video_queue.batch_status
             WHERE request_key=%s
            """,
            (request_key,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    result = dict(row)
    result["batch_id"] = str(result["batch_id"])
    for key in ("created_at", "updated_at", "completed_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    result["schema"] = "universal-video-batch-status-v1"
    return result


def claim_job(
    database_url: str,
    worker_key: str,
    lease_seconds: int,
    *,
    processing_profile: str,
    algorithm_revision: str,
) -> dict[str, Any] | None:
    from psycopg import connect
    from psycopg.rows import dict_row

    with connect(database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM video_queue.claim_job(%s,%s,%s,%s)",
            (worker_key, lease_seconds, processing_profile, algorithm_revision),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return {key: (str(value) if key in {"job_id", "batch_id", "lease_token"} else value) for key, value in row.items()}


def heartbeat_job(
    database_url: str,
    *,
    job_id: str,
    lease_token: str,
    worker_key: str,
    extend_seconds: int = 900,
) -> None:
    from psycopg import connect

    with connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT video_queue.heartbeat_job(%s,%s,%s,%s)",
            (job_id, lease_token, worker_key, extend_seconds),
        )
        if cursor.fetchone() is None:
            raise VideoQueueError("Neon heartbeat returned no receipt")


def retry_job(
    database_url: str,
    *,
    job_id: str,
    lease_token: str,
    worker_key: str,
    error_code: str,
    max_attempts: int = 3,
    base_delay_seconds: int = 60,
) -> dict[str, Any]:
    from psycopg import connect
    from psycopg.rows import dict_row

    with connect(database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM video_queue.retry_job(%s,%s,%s,%s,%s,%s)",
            (job_id, lease_token, worker_key, error_code, max_attempts, base_delay_seconds),
        )
        row = cursor.fetchone()
    if not row:
        raise VideoQueueError("Neon retry returned no receipt")
    result = dict(row)
    if result.get("retry_after") is not None:
        result["retry_after"] = result["retry_after"].isoformat()
    return result


def finish_job(
    database_url: str,
    *,
    job_id: str,
    lease_token: str,
    worker_key: str,
    outcome: str,
    output: Mapping[str, Any],
    error_code: str | None = None,
) -> dict[str, Any]:
    from psycopg import connect
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb

    with connect(database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT * FROM video_queue.finish_job(%s,%s,%s,%s,%s,%s)",
            (job_id, lease_token, worker_key, outcome, Jsonb(dict(output)), error_code),
        )
        row = cursor.fetchone()
    if not row:
        raise VideoQueueError("Neon finish returned no receipt")
    return dict(row)


__all__ = [
    "INTAKE_FIELDS",
    "MAX_FILES",
    "VideoQueueError",
    "batch_status",
    "build_drive_manifest",
    "claim_job",
    "database_url_from_env",
    "discover_drive_manifest",
    "enqueue_drive_request",
    "enqueue_manifest",
    "finish_job",
    "heartbeat_job",
    "inventory_sha256",
    "normalize_drive_inventory",
    "retry_job",
    "validate_intake_request",
]
