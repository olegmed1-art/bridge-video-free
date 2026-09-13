"""Fail-closed job contract for the universal educational video analyzer."""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .profiles import resolve_profile

CONTRACT_VERSION = "universal-video-v1"
MAX_JOB_BYTES = 256 * 1024
MAX_VIDEO_SECONDS = 12 * 3600
MAX_SOURCE_BYTES = 64 * 1024**3
MIN_SOURCE_BYTES = 1024**2
MIN_FRAME_INTERVAL_SECONDS = 15
MAX_FRAME_INTERVAL_SECONDS = 3600
ALLOWED_SOURCE_KINDS = frozenset({"local_path", "google_drive", "oracle_drive_staged"})
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")
RESERVED_PATH_IDS = frozenset({".", ".."})


class VideoContractError(ValueError):
    pass


@dataclass(frozen=True)
class VideoJob:
    job_id: str
    profile: str
    source: dict[str, Any]
    project: str | None
    metadata: dict[str, Any]
    options: dict[str, Any]


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise VideoContractError(f"{field} must be an object")
    return dict(value)


def _bounded_text(value: Any, field: str, *, max_len: int = 500) -> str:
    text = str(value or "").strip()
    if not text or len(text) > max_len:
        raise VideoContractError(f"invalid {field}")
    return text


def _validate_local_path(source: dict[str, Any], *, allowed_root: str | None) -> dict[str, Any]:
    raw = _bounded_text(source.get("path"), "source.path", max_len=4096)
    path = Path(raw)
    if not path.is_absolute():
        raise VideoContractError("local_path source must be absolute")
    resolved = path.resolve()
    if allowed_root:
        root = Path(allowed_root).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise VideoContractError("local_path escapes configured media root") from exc
    return {"kind": "local_path", "path": str(resolved)}


def _validate_drive(source: dict[str, Any]) -> dict[str, Any]:
    file_id = _bounded_text(source.get("file_id"), "source.file_id", max_len=200)
    if not DRIVE_ID_RE.fullmatch(file_id):
        raise VideoContractError("invalid Google Drive file id")
    out = {"kind": "google_drive", "file_id": file_id}
    name = str(source.get("name") or "").strip()
    if name:
        out["name"] = name[:500]
    return out


def _validate_oracle_drive_staged(source: dict[str, Any], *, allowed_root: str | None) -> dict[str, Any]:
    local = _validate_local_path(source, allowed_root=allowed_root)
    drive = _validate_drive(source)
    sha256 = _bounded_text(source.get("sha256"), "source.sha256", max_len=64).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise VideoContractError("invalid staged source sha256")
    try:
        size_bytes = int(source.get("size_bytes"))
    except (TypeError, ValueError) as exc:
        raise VideoContractError("invalid staged source size") from exc
    if not MIN_SOURCE_BYTES <= size_bytes <= MAX_SOURCE_BYTES:
        raise VideoContractError("staged source size outside bounded range")
    return {
        "kind": "oracle_drive_staged",
        "path": local["path"],
        "file_id": drive["file_id"],
        **({"name": drive["name"]} if "name" in drive else {}),
        "drive_name": str(source.get("drive_name") or source.get("name") or "")[:500],
        "mime_type": str(source.get("mime_type") or "application/octet-stream")[:200],
        "size_bytes": size_bytes,
        "sha256": sha256,
        "modified_time": str(source.get("modified_time") or "")[:100],
        "md5": str(source.get("md5") or "").lower()[:64],
    }


def _bounded_int(options: dict[str, Any], key: str, minimum: int, maximum: int) -> None:
    if key not in options:
        return
    try:
        value = int(options[key])
    except (TypeError, ValueError) as exc:
        raise VideoContractError(f"{key} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise VideoContractError(f"{key} outside bounded range")
    options[key] = value


def validate_job(payload: Any, *, allowed_local_root: str | None = None) -> VideoJob:
    data = _mapping(payload, "job")
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_JOB_BYTES:
        raise VideoContractError("job payload exceeds bounded contract")

    job_id = _bounded_text(data.get("job_id"), "job_id", max_len=160)
    if not ID_RE.fullmatch(job_id) or job_id in RESERVED_PATH_IDS:
        raise VideoContractError("invalid job_id")

    profile = _bounded_text(data.get("profile"), "profile", max_len=80).lower()
    resolve_profile(profile)

    source = _mapping(data.get("source"), "source")
    kind = _bounded_text(source.get("kind"), "source.kind", max_len=40).lower()
    if kind not in ALLOWED_SOURCE_KINDS:
        raise VideoContractError("unsupported source kind")
    if kind == "local_path":
        source = _validate_local_path(source, allowed_root=allowed_local_root)
    elif kind == "google_drive":
        source = _validate_drive(source)
    else:
        source = _validate_oracle_drive_staged(source, allowed_root=allowed_local_root)

    project = str(data.get("project") or "").strip() or None
    if project and len(project) > 160:
        raise VideoContractError("project is too long")

    metadata = _mapping(data.get("metadata"), "metadata")
    options = _mapping(data.get("options"), "options")
    if "max_duration_seconds" in options:
        try:
            value = float(options["max_duration_seconds"])
        except (TypeError, ValueError) as exc:
            raise VideoContractError("max_duration_seconds must be numeric") from exc
        if not 1 <= value <= MAX_VIDEO_SECONDS:
            raise VideoContractError("max_duration_seconds outside bounded range")
        options["max_duration_seconds"] = value

    _bounded_int(options, "chunk_seconds", 60, 900)
    _bounded_int(
        options,
        "frame_interval_seconds",
        MIN_FRAME_INTERVAL_SECONDS,
        MAX_FRAME_INTERVAL_SECONDS,
    )
    _bounded_int(options, "max_source_bytes", MIN_SOURCE_BYTES, MAX_SOURCE_BYTES)

    return VideoJob(
        job_id=job_id,
        profile=profile,
        source=source,
        project=project,
        metadata=metadata,
        options=options,
    )


def canonical_job_hash(job: VideoJob) -> str:
    source = job.source
    if source.get("kind") == "oracle_drive_staged":
        source = {"kind": "google_drive", "file_id": source["file_id"]}
        if job.source.get("name"):
            source["name"] = job.source["name"]
    payload = {
        "contract": CONTRACT_VERSION,
        "job_id": job.job_id,
        "profile": job.profile,
        "source": source,
        "project": job.project,
        "metadata": job.metadata,
        "options": job.options,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_from_env(payload: Any) -> VideoJob:
    root = os.getenv("UNIVERSAL_VIDEO_MEDIA_ROOT", "").strip() or None
    job = validate_job(payload, allowed_local_root=root)
    if os.getenv("UNIVERSAL_VIDEO_REQUIRE_STAGED_SOURCE", "0").strip() == "1" and job.source.get("kind") != "oracle_drive_staged":
        raise VideoContractError("production worker accepts staged Drive sources only")
    return job


__all__ = [
    "CONTRACT_VERSION",
    "MAX_FRAME_INTERVAL_SECONDS",
    "MAX_JOB_BYTES",
    "MAX_SOURCE_BYTES",
    "MAX_VIDEO_SECONDS",
    "MIN_FRAME_INTERVAL_SECONDS",
    "MIN_SOURCE_BYTES",
    "RESERVED_PATH_IDS",
    "VideoContractError",
    "VideoJob",
    "canonical_job_hash",
    "validate_from_env",
    "validate_job",
]
