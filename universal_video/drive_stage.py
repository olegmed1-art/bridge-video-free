"""Atomic Google Drive to Oracle staging for Universal Video jobs."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .contract import MAX_SOURCE_BYTES, VideoJob
from .drive_adapter import access_token, download_file, file_metadata


class DriveStageError(RuntimeError):
    """A bounded Drive-to-Oracle transfer failure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".video"


def stage_drive_job(job: VideoJob, payload: dict[str, Any], media_root: Path) -> tuple[dict[str, Any], Path]:
    """Download one Drive source, verify it, and return an internal staged job."""
    if job.source.get("kind") != "google_drive":
        raise DriveStageError("Drive staging accepts google_drive sources only")
    if not media_root.is_dir() or media_root.is_symlink():
        raise DriveStageError("unsafe Oracle media root")

    ready_root = media_root / "drive-ready"
    ready_root.mkdir(mode=0o750, exist_ok=True)
    if ready_root.is_symlink():
        raise DriveStageError("unsafe Oracle Drive staging root")
    job_dir = ready_root / job.job_id
    if job_dir.exists() and (not job_dir.is_dir() or job_dir.is_symlink()):
        raise DriveStageError("unsafe Oracle Drive job staging path")
    job_dir.mkdir(mode=0o750, exist_ok=True)

    token = access_token()
    meta = file_metadata(str(job.source["file_id"]), token)
    try:
        declared_size = int(meta.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise DriveStageError("Drive source size is unavailable") from exc
    max_bytes = int(job.options.get("max_source_bytes") or MAX_SOURCE_BYTES)
    if not 0 < declared_size <= max_bytes:
        raise DriveStageError("Drive source size is outside configured bounds")

    drive_name = str(meta.get("name") or job.source.get("name") or "source.video")
    request_name = str(job.source.get("name") or "").strip()
    final = job_dir / f"source{_safe_suffix(drive_name)}"
    partial = job_dir / f".{final.name}.part"
    expected_sha = str(meta.get("sha256Checksum") or "").strip().lower()
    if final.exists():
        if final.is_symlink() or not final.is_file() or final.stat().st_size != declared_size:
            raise DriveStageError("existing staged Drive source is not reusable")
        observed_sha = _sha256(final)
        if expected_sha and observed_sha != expected_sha:
            raise DriveStageError("existing staged Drive source checksum mismatch")
        downloaded = dict(meta)
        downloaded["_download_sha256"] = observed_sha
    else:
        partial.unlink(missing_ok=True)
        downloaded = download_file(
            str(job.source["file_id"]),
            partial,
            token,
            max_bytes=max_bytes,
            metadata=meta,
        )
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(partial, final)
        directory_fd = os.open(job_dir, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    staged = dict(payload)
    staged["source"] = {
        "kind": "oracle_drive_staged",
        "path": str(final.resolve()),
        "file_id": str(job.source["file_id"]),
        **({"name": request_name[:500]} if request_name else {}),
        "drive_name": drive_name[:500],
        "mime_type": str(downloaded.get("mimeType") or "application/octet-stream")[:200],
        "size_bytes": declared_size,
        "sha256": str(downloaded.get("_download_sha256") or _sha256(final)).lower(),
        "modified_time": str(downloaded.get("modifiedTime") or "")[:100],
        "md5": str(downloaded.get("md5Checksum") or "").lower()[:64],
    }
    return staged, job_dir


def remove_staged_job(job_dir: Path, media_root: Path) -> None:
    """Remove only a resolved per-job staging directory after a terminal receipt."""
    root = (media_root / "drive-ready").resolve()
    resolved = job_dir.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise DriveStageError("staged cleanup path escapes Oracle media root") from exc
    if len(relative.parts) != 1 or not resolved.is_dir() or resolved.is_symlink():
        raise DriveStageError("unsafe staged cleanup target")
    shutil.rmtree(resolved)


__all__ = ["DriveStageError", "remove_staged_job", "stage_drive_job"]
