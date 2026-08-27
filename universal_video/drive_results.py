"""Bounded publication of compact Universal Video artifacts to Google Drive.

Raw video/audio is deliberately outside the publication allow-list. The module
can probe a destination folder and can publish one terminal result directory in
an idempotent child folder keyed by the complete compact artifact-set hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import secrets
import stat
import tempfile
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .drive_adapter import DRIVE, access_token
from .result_conformance import ResultConformanceError, verify_result

UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
TOP_LEVEL_ALLOWLIST = frozenset({"manifest.json", "transcript.jsonl", "transcript.txt", "transcript_qc.json"})
FRAME_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
PUBLISHABLE_STATUSES = frozenset({"COMPLETED"})
RAW_EXTENSIONS = frozenset({".mp4", ".mkv", ".mov", ".avi", ".webm", ".wav", ".mp3", ".m4a", ".flac"})
SAFE_NAME = re.compile(r"[^A-Za-zА-Яа-яЁё0-9._ -]+")
MULTIPART_UPLOAD_MAX_BYTES = 5 * 1024**2


@dataclass(frozen=True)
class PublishArtifact:
    path: Path
    relative_name: str
    size_bytes: int
    sha256: str
    md5: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _md5(path: Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_set_sha256(artifacts: list[PublishArtifact]) -> str:
    inventory = [
        [item.relative_name, item.size_bytes, item.sha256]
        for item in sorted(artifacts, key=lambda value: value.relative_name)
    ]
    raw = json.dumps(inventory, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


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
    max_file_bytes: int = MULTIPART_UPLOAD_MAX_BYTES,
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
    if not isinstance(manifest, dict) or str(manifest.get("status") or "").upper() not in PUBLISHABLE_STATUSES:
        raise RuntimeError("result is not technical COMPLETED/publishable")
    if not all(str(manifest.get(key) or "").strip() for key in ("job_id", "job_hash", "profile")):
        raise RuntimeError("publishable manifest identity is incomplete")
    required = TOP_LEVEL_ALLOWLIST
    missing = sorted(name for name in required if not (job_dir / name).exists())
    if missing:
        raise RuntimeError(f"required compact artifacts missing: {','.join(missing)}")

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
        artifacts.append(PublishArtifact(path, relative, size, _sha256(path), _md5(path)))
    return artifacts


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _verify_folder(
    folder_id: str,
    token: str,
    *,
    expected_parent_id: str | None = None,
    require_writable: bool = False,
) -> dict[str, Any]:
    response = requests.get(
        f"{DRIVE}/files/{folder_id}",
        headers=_headers(token),
        params={
            "fields": "id,name,mimeType,trashed,parents,driveId,capabilities(canAddChildren),permissions(id,type,role)",
            "supportsAllDrives": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if str(data.get("mimeType") or "") != FOLDER_MIME or bool(data.get("trashed")):
        raise RuntimeError("Drive result folder is not active")
    parents = data.get("parents") if isinstance(data.get("parents"), list) else []
    if expected_parent_id is not None and expected_parent_id not in parents:
        raise RuntimeError("Drive result child has unexpected parent")
    capabilities = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}
    if require_writable and capabilities.get("canAddChildren") is not True:
        raise RuntimeError("Drive results destination is not writable")
    permissions = data.get("permissions")
    if not isinstance(permissions, list) or not permissions:
        raise RuntimeError("Drive results destination ACL is unavailable")
    if any(str(item.get("type") or "") in {"anyone", "domain"} for item in permissions if isinstance(item, dict)):
        raise RuntimeError("Drive results destination has broad ACL")
    return {
        "status": "PASS",
        "mime_type": FOLDER_MIME,
        "can_add_children": capabilities.get("canAddChildren") is True,
        "broad_acl": False,
        "permission_count": len(permissions),
    }


def probe_destination(folder_id: str, token: str | None = None) -> dict[str, Any]:
    return _verify_folder(folder_id, token or access_token(), require_writable=True)


def _escape_q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_child_folder(parent_id: str, name: str, token: str) -> str | None:
    query = f"'{_escape_q(parent_id)}' in parents and name = '{_escape_q(name)}' and mimeType = '{FOLDER_MIME}' and trashed = false"
    response = requests.get(
        f"{DRIVE}/files",
        headers=_headers(token),
        params={
            "q": query,
            "fields": "nextPageToken,files(id,name)",
            "pageSize": 1000,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    files = payload.get("files") or []
    if payload.get("nextPageToken") or len(files) > 1:
        raise RuntimeError("duplicate Drive result folders")
    return str(files[0]["id"]) if files else None


def _create_folder(parent_id: str, name: str, token: str) -> str:
    response = requests.post(
        f"{DRIVE}/files",
        headers={**_headers(token), "Content-Type": "application/json"},
        params={"supportsAllDrives": True, "ignoreDefaultVisibility": True, "fields": "id"},
        json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        timeout=30,
    )
    response.raise_for_status()
    folder_id = str(response.json()["id"])
    unique = _find_child_folder(parent_id, name, token)
    if unique != folder_id:
        raise RuntimeError("Drive result folder creation was not unique")
    return folder_id


def _find_existing_file(parent_id: str, name: str, token: str) -> dict[str, Any] | None:
    query = f"'{_escape_q(parent_id)}' in parents and name = '{_escape_q(name)}' and trashed = false"
    response = requests.get(
        f"{DRIVE}/files",
        headers=_headers(token),
        params={
            "q": query,
            "fields": "nextPageToken,files(id,name,size,md5Checksum,mimeType,appProperties,permissions(id,type,role),trashed)",
            "pageSize": 1000,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    files = payload.get("files") or []
    if payload.get("nextPageToken") or len(files) > 1:
        raise RuntimeError(f"duplicate Drive result file: {name}")
    return dict(files[0]) if files else None


def _get_file_metadata(file_id: str, token: str) -> dict[str, Any]:
    response = requests.get(
        f"{DRIVE}/files/{file_id}",
        headers=_headers(token),
        params={
            "fields": "id,name,size,md5Checksum,mimeType,appProperties,permissions(id,type,role),trashed",
            "supportsAllDrives": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    return dict(response.json())


def _verify_remote_artifact(remote: dict[str, Any], artifact: PublishArtifact) -> dict[str, Any]:
    properties = remote.get("appProperties") if isinstance(remote.get("appProperties"), dict) else {}
    try:
        remote_size = int(remote.get("size"))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Drive result size is missing: {artifact.relative_name}") from exc
    if remote_size != artifact.size_bytes:
        raise RuntimeError(f"Drive result size mismatch: {artifact.relative_name}")
    if str(remote.get("md5Checksum") or "").lower() != artifact.md5:
        raise RuntimeError(f"Drive result checksum mismatch: {artifact.relative_name}")
    if str(properties.get("sha256") or "").lower() != artifact.sha256:
        raise RuntimeError(f"Drive result SHA-256 property mismatch: {artifact.relative_name}")
    if bool(remote.get("trashed")):
        raise RuntimeError(f"Drive result is trashed: {artifact.relative_name}")
    permissions = remote.get("permissions")
    if not isinstance(permissions, list) or not permissions:
        raise RuntimeError(f"Drive result ACL is unavailable: {artifact.relative_name}")
    if any(str(item.get("type") or "") in {"anyone", "domain"} for item in permissions if isinstance(item, dict)):
        raise RuntimeError(f"Drive result has broad ACL: {artifact.relative_name}")
    return {
        "relative_name": artifact.relative_name,
        "file_id": str(remote.get("id") or ""),
        "size_bytes": artifact.size_bytes,
        "md5": artifact.md5,
        "sha256": artifact.sha256,
    }


def _upload_or_verify_file(parent_id: str, artifact: PublishArtifact, token: str) -> dict[str, Any]:
    upload_name = artifact.relative_name.replace("/", "__")
    existing = _find_existing_file(parent_id, upload_name, token)
    if existing is not None:
        return _verify_remote_artifact(_get_file_metadata(str(existing.get("id") or ""), token), artifact)
    mime = mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
    metadata = {"name": upload_name, "parents": [parent_id], "appProperties": {"sha256": artifact.sha256}}
    media = artifact.path.read_bytes()
    if (
        len(media) != artifact.size_bytes
        or hashlib.sha256(media).hexdigest() != artifact.sha256
        or hashlib.md5(media, usedforsecurity=False).hexdigest() != artifact.md5
    ):
        raise RuntimeError(f"local artifact changed before upload: {artifact.relative_name}")
    boundary = f"bridge-school-{secrets.token_hex(24)}"
    while boundary.encode("ascii") in media:
        boundary = f"bridge-school-{secrets.token_hex(24)}"
    metadata_bytes = json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    body = b"".join(
        (
            f"--{boundary}\r\n".encode("ascii"),
            b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
            metadata_bytes,
            f"\r\n--{boundary}\r\n".encode("ascii"),
            f"Content-Type: {mime}\r\n\r\n".encode("ascii"),
            media,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        )
    )
    response = requests.post(
        UPLOAD,
        headers={**_headers(token), "Content-Type": f"multipart/related; boundary={boundary}"},
        params={
            "uploadType": "multipart",
            "fields": "id",
            "supportsAllDrives": True,
            "ignoreDefaultVisibility": True,
        },
        data=body,
        timeout=180,
    )
    response.raise_for_status()
    remote = _get_file_metadata(str(response.json()["id"]), token)
    return _verify_remote_artifact(remote, artifact)


def _list_children(parent_id: str, token: str) -> list[dict[str, Any]]:
    query = f"'{_escape_q(parent_id)}' in parents and trashed = false"
    response = requests.get(
        f"{DRIVE}/files",
        headers=_headers(token),
        params={
            "q": query,
            "fields": "nextPageToken,files(id,name,size,md5Checksum,mimeType,appProperties,permissions(id,type,role),trashed)",
            "pageSize": 1000,
            "supportsAllDrives": True,
            "includeItemsFromAllDrives": True,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("nextPageToken"):
        raise RuntimeError("Drive publication inventory exceeds verification page")
    return [dict(item) for item in (payload.get("files") or [])]


def _verify_remote_inventory(
    child_id: str,
    artifacts: list[PublishArtifact],
    token: str,
) -> list[dict[str, Any]]:
    expected = {item.relative_name.replace("/", "__"): item for item in artifacts}
    remote_files = _list_children(child_id, token)
    by_name: dict[str, dict[str, Any]] = {}
    for item in remote_files:
        name = str(item.get("name") or "")
        if name in by_name:
            raise RuntimeError(f"duplicate Drive publication child: {name}")
        by_name[name] = item
    if set(by_name) != set(expected):
        raise RuntimeError("Drive publication inventory mismatch")
    return [_verify_remote_artifact(by_name[name], artifact) for name, artifact in sorted(expected.items())]


def publish_result(
    job_dir: Path,
    folder_id: str,
    *,
    expected_job_id: str,
    expected_profile: str,
    expected_job_hash: str,
    expected_source_file_id: str | None,
    expected_artifact_set_sha256: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    max_frames = int(os.getenv("UNIVERSAL_VIDEO_PUBLISH_MAX_FRAMES", "300"))
    max_file_bytes = int(os.getenv("UNIVERSAL_VIDEO_PUBLISH_MAX_FILE_BYTES", str(MULTIPART_UPLOAD_MAX_BYTES)))
    if max_file_bytes > MULTIPART_UPLOAD_MAX_BYTES:
        raise RuntimeError("multipart publication file cap cannot exceed 5 MiB")
    max_total_bytes = int(os.getenv("UNIVERSAL_VIDEO_PUBLISH_MAX_TOTAL_BYTES", str(256 * 1024**2)))
    artifacts = collect_compact_artifacts(
        job_dir,
        max_frames=max_frames,
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
    )
    manifest = json.loads((job_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest_hash = next(item.sha256 for item in artifacts if item.relative_name == "manifest.json")
    bundle_hash = artifact_set_sha256(artifacts)
    conformance = verify_result(
        job_dir,
        expected_job_id=expected_job_id,
        expected_profile=expected_profile,
        expected_job_hash=expected_job_hash,
        expected_source_file_id=expected_source_file_id,
        expected_artifact_set_sha256=expected_artifact_set_sha256,
        evidence_phase="PUBLICATION_PREFLIGHT",
        max_file_bytes=max_file_bytes,
        max_total_bytes=max_total_bytes,
        max_frames=max_frames,
    )
    if conformance.get("artifact_set_sha256") != bundle_hash:
        raise RuntimeError("publication/conformance artifact inventory mismatch")
    if bundle_hash != expected_artifact_set_sha256:
        raise RuntimeError("publication bundle changed after approval")
    safe_job = SAFE_NAME.sub("_", job_dir.name)[:100] or "job"
    child_name = f"{safe_job}-{bundle_hash[:12]}"
    report = {
        "status": "DRY_RUN_READY" if dry_run else "PUBLISHED_VERIFIED",
        "artifact_count": len(artifacts),
        "total_bytes": sum(item.size_bytes for item in artifacts),
        "child_folder": child_name,
        "manifest_sha256": manifest_hash,
        "artifact_set_sha256": bundle_hash,
        "local_conformance": "PASS",
        "domain_analysis_status": conformance.get("domain_analysis_status"),
        "pedagogical_status": conformance.get("pedagogical_status"),
        "raw_media_included": False,
    }
    if dry_run:
        return report

    token = access_token()
    probe_destination(folder_id, token)
    child_id = _find_child_folder(folder_id, child_name, token) or _create_folder(folder_id, child_name, token)
    _verify_folder(child_id, token, expected_parent_id=folder_id, require_writable=True)
    for artifact in artifacts:
        _upload_or_verify_file(child_id, artifact, token)
    _verify_remote_inventory(child_id, artifacts, token)
    marker_payload = {
        "schema": "universal-video-publication-marker-v1",
        "status": "PUBLICATION_COMPLETE",
        "job_id": manifest.get("job_id"),
        "job_hash": manifest.get("job_hash"),
        "source_fingerprint": manifest.get("source_fingerprint"),
        "processing_fingerprint": manifest.get("processing_fingerprint"),
        "manifest_sha256": manifest_hash,
        "artifact_set_sha256": bundle_hash,
        "artifacts": [
            {"relative_name": item.relative_name, "size_bytes": item.size_bytes, "sha256": item.sha256}
            for item in sorted(artifacts, key=lambda value: value.relative_name)
        ],
    }
    with tempfile.TemporaryDirectory(prefix="universal-video-publication-") as temp:
        marker_path = Path(temp) / "PUBLICATION_COMPLETE.json"
        marker_path.write_text(
            json.dumps(marker_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        marker = PublishArtifact(
            marker_path,
            marker_path.name,
            marker_path.stat().st_size,
            _sha256(marker_path),
            _md5(marker_path),
        )
        _upload_or_verify_file(child_id, marker, token)
        verified = _verify_remote_inventory(child_id, [*artifacts, marker], token)
    report["child_folder_id"] = child_id
    report["publication_marker_sha256"] = marker.sha256
    report["remote_artifacts"] = verified
    report["remote_verification"] = "SIZE_MD5_SHA256_PROPERTY_MATCH"
    report["verified_at"] = datetime.now(timezone.utc).isoformat()
    _verify_folder(child_id, token, expected_parent_id=folder_id, require_writable=True)
    proof = {
        "schema": "universal-video-durable-publication-proof-v1",
        "status": "PUBLISHED_VERIFIED",
        "job_id": manifest.get("job_id"),
        "job_hash": manifest.get("job_hash"),
        "drive_folder_id": child_id,
        "artifact_set_sha256": bundle_hash,
        "publication_marker_sha256": marker.sha256,
        "remote_verification": "SIZE_MD5_SHA256_PROPERTY_MATCH",
        "verified_at": report["verified_at"],
    }
    proof_path = job_dir / "DURABLE_PUBLICATION_PROOF.json"
    proof_tmp = job_dir / ".DURABLE_PUBLICATION_PROOF.json.tmp"
    proof_tmp.write_text(
        json.dumps(proof, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.replace(proof_tmp, proof_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    probe = sub.add_parser("probe-destination")
    probe.add_argument("--folder-id", required=True)
    publish = sub.add_parser("publish")
    publish.add_argument("--folder-id", required=True)
    publish.add_argument("--job-dir", required=True)
    publish.add_argument("--expected-job-id", required=True)
    publish.add_argument("--expected-profile", required=True)
    publish.add_argument("--expected-job-hash", required=True)
    publish.add_argument("--expected-source-file-id")
    publish.add_argument("--expected-artifact-set-sha256", required=True)
    publish.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "probe-destination":
        print(json.dumps(probe_destination(args.folder_id), sort_keys=True), flush=True)
        return
    print(
        json.dumps(
            publish_result(
                Path(args.job_dir),
                args.folder_id,
                expected_job_id=args.expected_job_id,
                expected_profile=args.expected_profile,
                expected_job_hash=args.expected_job_hash,
                expected_source_file_id=args.expected_source_file_id,
                expected_artifact_set_sha256=args.expected_artifact_set_sha256,
                dry_run=args.dry_run,
            ),
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
