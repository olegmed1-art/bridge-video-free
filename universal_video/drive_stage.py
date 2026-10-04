"""Atomic Google Drive to Oracle staging for Universal Video jobs."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .contract import MAX_SOURCE_BYTES, VideoJob, canonical_job_hash
from .drive_adapter import access_token, download_file, file_metadata, original_snapshot
from .durable_drive import SOURCE_RECEIPT, atomic_json, read_receipt


class DriveStageError(RuntimeError):
    """A bounded Drive-to-Oracle transfer failure."""

    def __init__(self, message: str, *, error_code: str = "UV_DRIVE_STAGE_FAILED") -> None:
        self.error_code = error_code
        super().__init__(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_suffix(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,10}", suffix) else ".video"


def _require_video_mime(meta: dict[str, Any]) -> str:
    """Reject non-video Drive objects before they consume Oracle media space."""

    mime = str(meta.get("mimeType") or "").strip().lower()
    if not mime.startswith("video/"):
        raise DriveStageError(
            "Drive source MIME type is not a video",
            error_code="UV_DRIVE_SOURCE_MIME_UNSUPPORTED",
        )
    return mime


def _verify_staged_bytes(path: Path, *, declared_size: int, expected_sha: str) -> str:
    """Require a complete local source before it can leave ``.part``."""

    try:
        observed_size = path.stat().st_size
    except OSError as exc:
        raise DriveStageError(
            "staged Drive source is unavailable",
            error_code="UV_DRIVE_SOURCE_UNAVAILABLE",
        ) from exc
    if observed_size != declared_size:
        raise DriveStageError(
            "staged Drive source size mismatch",
            error_code="UV_DRIVE_SOURCE_SIZE_MISMATCH",
        )
    observed_sha = _sha256(path)
    if expected_sha and observed_sha != expected_sha:
        raise DriveStageError(
            "staged Drive source checksum mismatch",
            error_code="UV_DRIVE_SOURCE_CHECKSUM_MISMATCH",
        )
    return observed_sha


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
    mime_type = _require_video_mime(meta)

    drive_name = str(meta.get("name") or job.source.get("name") or "source.video")
    request_name = str(job.source.get("name") or "").strip()
    final = job_dir / f"source{_safe_suffix(drive_name)}"
    partial = job_dir / f".{final.name}.part"
    expected_sha = str(meta.get("sha256Checksum") or "").strip().lower()
    original = original_snapshot(meta)
    pin = job_dir / SOURCE_RECEIPT
    previous = read_receipt(pin) if pin.exists() else None
    if previous is None and any(job_dir.glob("source*")):
        raise DriveStageError("existing source has no immutable pin; retain cache for review")
    job_hash = canonical_job_hash(job)
    # The receipt is the source binding, not a cache hint. Check it even when
    # the cached file disappeared or the current metadata chooses a new suffix.
    if previous is not None:
        if (previous.get("schema") != "universal-video-source-integrity-v1"
                or previous.get("job_id") != job.job_id or previous.get("job_hash") != job_hash
                or previous.get("original") != original
                or not re.fullmatch(r"[0-9a-f]{64}", str(previous.get("sha256") or ""))):
            raise DriveStageError("Drive original/job binding changed; existing pin retained",
                                  error_code="UV_DRIVE_SOURCE_IDENTITY_CHANGED")
        if expected_sha and expected_sha != previous["sha256"]:
            raise DriveStageError("Drive original checksum changed; existing pin retained",
                                  error_code="UV_DRIVE_SOURCE_CHECKSUM_MISMATCH")
    if final.exists() and previous is not None:
        if final.is_symlink() or not final.is_file():
            raise DriveStageError("existing staged Drive source is not reusable")
        observed_sha = _verify_staged_bytes(final, declared_size=declared_size, expected_sha=previous["sha256"])
        if expected_sha and observed_sha != expected_sha:
            raise DriveStageError("Drive source checksum changed")
        downloaded = dict(meta)
        downloaded["_download_sha256"] = observed_sha
    else:
        if final.is_symlink() or partial.is_symlink():
            raise DriveStageError("unsafe staged source path")
        # Never Range-resume an unbound partial; restart from zero privately.
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
        observed_sha = _verify_staged_bytes(partial, declared_size=declared_size,
                                             expected_sha=previous["sha256"] if previous is not None else expected_sha)
        if original_snapshot(file_metadata(str(job.source["file_id"]), token)) != original:
            raise DriveStageError("Drive original changed during download")
        # Pin before final rename. A crash either reuses matching complete bytes
        # or restarts an unpublished partial; it cannot rebind an old cache.
        if previous is None:
            atomic_json(pin, {"schema": "universal-video-source-integrity-v1",
                              "original": original, "sha256": observed_sha,
                              "job_id": job.job_id, "job_hash": job_hash,
                              "transfer_mode": "RESTART_NO_PARTIAL_RESUME"})
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
        "mime_type": mime_type,
        "size_bytes": declared_size,
        "sha256": str(downloaded.get("_download_sha256") or _sha256(final)).lower(),
        "modified_time": str(downloaded.get("modifiedTime") or "")[:100],
        "md5": str(downloaded.get("md5Checksum") or "").lower()[:64],
    }
    return staged, job_dir


def remove_staged_job(job_dir: Path, media_root: Path) -> None:
    """Remove only a resolved per-job staging directory after a terminal receipt."""
    if media_root.is_symlink() or (media_root / "drive-ready").is_symlink() or job_dir.is_symlink():
        raise DriveStageError("unsafe staged cleanup symlink")
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
