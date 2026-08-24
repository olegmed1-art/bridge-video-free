"""Secret-safe Google Drive preflight for Universal Video.

This module validates the file-backed OAuth boundary and can exercise one Drive
video metadata/download/ffprobe path without importing or starting ASR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .contract import MAX_SOURCE_BYTES, MAX_VIDEO_SECONDS, MIN_SOURCE_BYTES
from .drive_adapter import access_token, download_file, file_metadata


def credential_boundary_status() -> str:
    file_name = os.getenv("GOOGLE_DRIVE_OAUTH_JSON_FILE", "").strip()
    if file_name:
        path = Path(file_name)
        if not path.is_absolute():
            return "NOT_CONFIGURED"
        try:
            info = path.lstat()
        except OSError:
            return "NOT_CONFIGURED"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            return "NOT_CONFIGURED"
        if info.st_mode & 0o007:
            return "NOT_CONFIGURED"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return "NOT_CONFIGURED"
        if not isinstance(data, dict):
            return "NOT_CONFIGURED"
        if all(isinstance(data.get(key), str) and data[key].strip() for key in ("client_id", "client_secret", "refresh_token")):
            return "CONFIGURED"
        return "NOT_CONFIGURED"

    direct = os.getenv("GOOGLE_DRIVE_OAUTH_JSON", "").strip()
    if direct:
        try:
            data = json.loads(direct)
        except json.JSONDecodeError:
            return "NOT_CONFIGURED"
        if isinstance(data, dict) and all(
            isinstance(data.get(key), str) and data[key].strip()
            for key in ("client_id", "client_secret", "refresh_token")
        ):
            return "CONFIGURED"
        return "NOT_CONFIGURED"

    legacy = [
        os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_ID", "").strip(),
        os.getenv("GOOGLE_DRIVE_OAUTH_CLIENT_SECRET", "").strip(),
        os.getenv("GOOGLE_DRIVE_OAUTH_REFRESH_TOKEN", "").strip(),
    ]
    return "CONFIGURED" if all(legacy) else "NOT_CONFIGURED"


def _bounded_source_limit(value: int) -> int:
    if not MIN_SOURCE_BYTES <= value <= MAX_SOURCE_BYTES:
        raise RuntimeError("max source bytes outside bounded range")
    return value


def _ffprobe(path: Path) -> dict[str, Any]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration,size,format_name:stream=index,codec_type,codec_name,width,height",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("ffprobe failed during Drive source preflight")
    data = json.loads(proc.stdout)
    duration = float((data.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("Drive source duration is unavailable")
    return {
        "duration_seconds": duration,
        "size_bytes": int((data.get("format") or {}).get("size") or path.stat().st_size),
        "format_name": str((data.get("format") or {}).get("format_name") or ""),
        "streams": data.get("streams") or [],
    }


def _source_fingerprint(file_id: str, size: int, sha256: str) -> str:
    payload = json.dumps(
        {"kind": "google_drive", "file_id": file_id, "size_bytes": size, "sha256": sha256},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def probe_drive_source(
    file_id: str,
    *,
    max_source_bytes: int,
    max_duration_seconds: float,
) -> dict[str, Any]:
    if credential_boundary_status() != "CONFIGURED":
        raise RuntimeError("Google Drive OAuth boundary is not configured")
    max_source_bytes = _bounded_source_limit(int(max_source_bytes))
    if not 1 <= float(max_duration_seconds) <= MAX_VIDEO_SECONDS:
        raise RuntimeError("max duration outside bounded range")

    token = access_token()
    meta = file_metadata(file_id, token)
    mime = str(meta.get("mimeType") or "")
    if not mime.startswith("video/"):
        raise RuntimeError("Drive source is not a video file")
    try:
        declared_size = int(meta.get("size") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Drive source size is unavailable") from exc
    if declared_size <= 0:
        raise RuntimeError("Drive source size is unavailable")
    if declared_size > max_source_bytes:
        raise RuntimeError("Drive source exceeds configured source-size limit")

    with tempfile.TemporaryDirectory(prefix="universal-video-drive-probe-") as temp:
        destination = Path(temp) / "source.video"
        downloaded = download_file(
            file_id,
            destination,
            token,
            max_bytes=max_source_bytes,
            metadata=meta,
        )
        probe = _ffprobe(destination)
        if probe["size_bytes"] > max_source_bytes:
            raise RuntimeError("downloaded source exceeds configured source-size limit")
        if probe["duration_seconds"] > float(max_duration_seconds) + 0.01:
            raise RuntimeError("Drive source exceeds configured duration limit")
        sha256 = str(downloaded.get("_download_sha256") or "").lower()
        if len(sha256) != 64:
            raise RuntimeError("download SHA-256 is unavailable")
        fingerprint = _source_fingerprint(file_id, probe["size_bytes"], sha256)

    return {
        "status": "PASS",
        "drive_oauth": "CONFIGURED",
        "source_kind": "google_drive",
        "mime_type": mime,
        "size_bytes": probe["size_bytes"],
        "duration_seconds": round(probe["duration_seconds"], 3),
        "format_name": probe["format_name"],
        "source_fingerprint": fingerprint,
        "checksum_verified": True,
        "raw_source_retained": False,
        "asr_started": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("credential-status")
    probe = sub.add_parser("source-probe")
    probe.add_argument("--file-id", required=True)
    probe.add_argument(
        "--max-source-bytes",
        type=int,
        default=int(os.getenv("UNIVERSAL_VIDEO_MAX_SOURCE_BYTES", str(16 * 1024**3))),
    )
    probe.add_argument("--max-duration-seconds", type=float, default=MAX_VIDEO_SECONDS)
    args = parser.parse_args()

    if args.command == "credential-status":
        print(f"UNIVERSAL_VIDEO_DRIVE_OAUTH={credential_boundary_status()}", flush=True)
        return
    report = probe_drive_source(
        args.file_id,
        max_source_bytes=args.max_source_bytes,
        max_duration_seconds=args.max_duration_seconds,
    )
    print(json.dumps(report, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
