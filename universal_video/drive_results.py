"""Bounded publication of compact Universal Video artifacts to Google Drive.

Raw video/audio is deliberately outside the publication allow-list. The module
can probe a destination folder and can publish one terminal result directory in
an idempotent child folder keyed by the manifest hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .drive_adapter import DRIVE, access_token

UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
TOP_LEVEL_ALLOWLIST = frozenset({"manifest.json", "transcript.jsonl", "transcript.txt", "transcript_qc.json"})
FRAME_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
TERMINAL_STATUSES = frozenset({"COMPLETED", "REVIEW"})
RAW_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".wav", ".mp3", ".m4a", ".flac"})
SAFE_NAME = re.compile(r"[^A-Za-zА-Яа-яЁё0-9._ -]+")


@dataclass(frozen=True)
class PublishArtifact:
    path: Path
    relative_name: str
    size_bytes: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_regular(path: Path, *, max_bytes: int) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and 0 <= info.st_size <= max_bytes


def collect_compact_artifacts(
    job_dir: Path,
    *,
    max_frames: int = 300,
    max_file_bytes: int = 8 * 1024**2,
    max_total_bytes: int = 256 * 1024**2,
) -> list[PublishArtifact]:
    if job_dir.is_symlink() or not job_dir.is_dir():
        raise RuntimeError("result directory must be a real directory")
    manifest_path = job_dir / "manifest.json"
    if not _safe_regular(manifest_path, max_bytes=max_file_bytes):
        raise RuntimeError("terminal manifest is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("terminal manifest is invalid") from exc
    if not isinstance(manifest, dict) or str(manifest.get("status") or "").upper() not in TERMINAL_STATUSES:
        raise RuntimeError("result is not terminal/publishable")

    selected: list[Path] = []
    for name in sorted(TOP_LEVEL_ALLOWLIST):
        path = job_dir / name
        if path.exists():
            if not _safe_regular(path, max_bytes=max_file_bytes):
                raise RuntimeError(f"unsafe compact artifact: {name}")
            selected.append(path)

    frames_dir = job_dir / "frames"
    if frames_dir.exists():
        if frames_dir.is_symlink() or not frames_dir.is_dir():
            raise RuntimeError("frames path is unsafe")
        frames: list[Path] = []
        for path in sorted(frames_dir.iterdir(), key=lambda item: item.name):
            if path.suffix.lower() not in FRAME_EXTENSIONS:
                continue
            if not _safe_regular(path, max_bytes=max_file_bytes):
                raise RuntimeError("unsafe keyframe artifact")
            frames.append(path)
        if len(frames) > max_frames:
            raise RuntimeError("keyframe count exceeds compact publication cap")
        selected.extend(frames)

    if not selected or manifest_path not in selected:
        raise RuntimeError("no compact result artifacts found")

    artifacts: list[PublishArtifact] = []
    total = 0
    for path in selected:
        if path.suffix.lower() in RAW_EXTENSIONS:
            raise RuntimeError("raw media publication is forbidden")
        relative = path.relative_to(job_dir).as_posix()
        size = path.stat().st_size
        total += size
        if total > max_total_bytes:
            raise RuntimeError("compact publication exceeds total byte cap")
        artifacts.append(PublishArtifact(path, relative, size, _sha256(path)))
    return artifacts


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def probe_destination(folder_id: str, token: str | None = None) -> dict[str, Any]:
    token = token or access_token()
    response = requests.get(
        f"{DRIVE}/files/{folder_id}",
        headers=_headers(token),
        params={"fields": "id,name,mimeType,trashed,capabilities(canAddChildren)"},
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("mimeType") or "") != FOLDER_MIME or bool(data.get("trashed")):
        raise RuntimeError("Drive results destination is not an active folder")
    capabilities = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    if capabilities.get("canAddChildren") is not True:
        raise RuntimeError("Drive results destination is not writable")
    return {"status": "PASS", "mime_type": FOLDER_MIME, "can_add_children": True}


def _escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_child_folder(parent_id: str, name: str, token: str) -> str | None:
    query = f"'{_escape_q(parent_id)}' in parents and name = '{_escape_q(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    response = requests.get(
        f"{DRIVE}/files",
        headers=_headers(token),
        params={"q": query, "fields": "files(id,name)", "pageSize": 10},
        timeout=30,
    )
    response.raise_for_status()
    files = response.json().get("files") or []
    return str(files[0]["id"]) if files else None


def _create_folder(parent_id: str, name: str, token: str) -> str:
    response = requests.post(
        f"{DRIVE}/files",
        headers={**_headers(token), "Content-Type": "application/json"},
        json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json()["id"])


def _existing_file(parent_id: str, name: str, token: str) -> bool:
    query = f"'{_escape_q(parent_id)}' in parents and name = '{_escape_q(name)}' and trashed = false"
    response = requests.get(
        f"{DRIVE}/files",
        headers=_headers(token),
        params={"q": query, "fields": "files(id)", "pageSize": 1},
        timeout=30,
    )
    response.raise_for_status()
    return bool(response.json().get("files"))


def _upload_file(parent_id: str, artifact: PublishArtifact, token: str) -> None:
    upload_name = artifact.relative_name.replace("/", "__")
    if _existing_file(parent_id, upload_name, token):
        return
    mime = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    metadata = {"name": upload_name, "parents": [parent_id], "appProperties": {"sha256": artifact.sha256}}
    with artifact.path.open("rb") as handle:
        response = requests.post(
            UPLOAD,
            headers=_headers(token),
            params={"uploadType": "multipart", "fields": "id"},
            files={
                "metadata": (None, json.dumps(metadata), "application/json; charset=UTF-8"),
                "file": (upload_name, handle, mime),
            },
            timeout=180,
        )
    response.raise_for_status()


def publish_result(job_dir: Path, folder_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    max_frames = int(os.getenv("UNIVERSAL_VIDEO_PUBLISH_MAX_FRAMES", "300"))
    max_file_bytes = int(os.getenv("UNIVERSAL_VIDEO_PUBLISH_MAX_FILE_BYTES", str(8 * 1024**2)))
    max_total_bytes = int(os.getenv("UNIVERSAL_VIDEO_PUBLISH_MAX_TOTAL_BYTES", str(256 * 1024**2)))
    artifacts = collect_compact_artifacts(
        job_dir,
        max_frames=max_frames,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    manifest_hash = next(item.sha256 for item in artifacts if item.relative_name == "manifest.json")
    safe_job = SAFE_NAME.sub("_", job_dir.name)[:100] or "job"
    child_name = f"{safe_job}-{manifest_hash[:12]}"
    report = {
        "status": "DRY_RUN" if dry_run else "PUBLISHED",
        "artifact_count": len(artifacts),
        "total_bytes": sum(item.size_bytes for item in artifacts),
        "child_folder": child_name,
        "raw_media_included": False,
    }
    if dry_run:
        return report

    token = access_token()
    probe_destination(folder_id, token)
    child_id = _find_child_folder(folder_id, child_name, token) or _create_folder(folder_id, child_name, token)
    for artifact in artifacts:
        _upload_file(child_id, artifact, token)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe-destination")
    probe.add_argument("--folder-id", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--folder-id", required=True)
    publish.add_argument("--job-dir", required=True)
    publish.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "probe-destination":
        print(json.dumps(probe_destination(args.folder_id), sort_keys=True), flush=True)
        return
    print(json.dumps(publish_result(Path(args.job_dir), args.folder_id, dry_run=args.dry_run), sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
