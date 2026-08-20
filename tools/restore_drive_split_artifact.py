#!/usr/bin/env python3
"""Restore a lifecycle P2/P3 artifact from verified Google Drive parts.

The locator contains only non-secret immutable identity: Drive file IDs, sizes and SHA-256.
OAuth credentials remain in GOOGLE_DRIVE_OAUTH_JSON and are consumed via the existing
zero-paid-cloud user OAuth adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import requests

from run_drive_3_1_free_oidc import user_oauth_token

DRIVE = "https://www.googleapis.com/drive/v3"


class RestoreError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid_sha(value: object) -> bool:
    text = str(value or "").lower()
    return len(text) == 64 and all(c in "0123456789abcdef" for c in text)


def validate_locator(data: dict) -> list[dict]:
    if data.get("schema") != "bridge-school-drive-artifact-v1":
        raise RestoreError("unsupported locator schema")
    if data.get("lifecycle_class") not in {"P2", "P3"}:
        raise RestoreError("restore helper only accepts P2/P3 locators")
    logical = data.get("logical_file") or {}
    try:
        logical_size = int(logical["size"])
    except Exception as exc:
        raise RestoreError("logical size missing or invalid") from exc
    if logical_size <= 0 or not _valid_sha(logical.get("sha256")):
        raise RestoreError("logical identity invalid")
    storage = data.get("storage") or {}
    if storage.get("provider") != "google_drive" or storage.get("layout") != "split-concatenate":
        raise RestoreError("unsupported storage layout")
    parts = storage.get("parts")
    if not isinstance(parts, list) or not parts:
        raise RestoreError("parts missing")
    normalized = []
    total = 0
    for expected_index, part in enumerate(parts, start=1):
        if not isinstance(part, dict) or int(part.get("index", -1)) != expected_index:
            raise RestoreError("part indexes must be contiguous and ordered")
        file_id = str(part.get("drive_file_id") or "")
        size = int(part.get("size") or 0)
        digest = str(part.get("sha256") or "").lower()
        if not file_id or size <= 0 or not _valid_sha(digest):
            raise RestoreError(f"part {expected_index} identity invalid")
        total += size
        normalized.append({"index": expected_index, "drive_file_id": file_id, "size": size, "sha256": digest})
    if total != logical_size:
        raise RestoreError("part sizes do not reconstruct logical size")
    return normalized


def reconstruct(parts: list[Path], output: Path, expected_size: int, expected_sha256: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as dst:
        for part in parts:
            with part.open("rb") as src:
                for chunk in iter(lambda: src.read(1024 * 1024), b""):
                    dst.write(chunk)
    if output.stat().st_size != expected_size:
        raise RestoreError("reconstructed size mismatch")
    observed = sha256_file(output)
    if observed != expected_sha256.lower():
        raise RestoreError(f"reconstructed SHA-256 mismatch: {observed}")


def download_part(token: str, spec: dict, path: Path) -> None:
    url = f"{DRIVE}/files/{spec['drive_file_id']}"
    with requests.get(url, headers={"Authorization": f"Bearer {token}"}, params={"alt": "media"}, stream=True, timeout=120) as r:
        if not r.ok:
            raise RestoreError(f"Drive download failed for part {spec['index']}: HTTP {r.status_code}")
        with path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
    if path.stat().st_size != spec["size"]:
        raise RestoreError(f"part {spec['index']} size mismatch")
    observed = sha256_file(path)
    if observed != spec["sha256"]:
        raise RestoreError(f"part {spec['index']} SHA-256 mismatch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--locator", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--work-dir", default="/tmp/bridge-drive-artifact-restore")
    ap.add_argument("--validate-only", action="store_true")
    args = ap.parse_args()

    locator_path = Path(args.locator)
    data = json.loads(locator_path.read_text(encoding="utf-8"))
    parts = validate_locator(data)
    logical = data["logical_file"]
    if args.validate_only:
        print(json.dumps({"status": "VALID", "parts": len(parts), "size": int(logical["size"]), "sha256": logical["sha256"]}, sort_keys=True))
        return 0

    token = user_oauth_token()
    if not token:
        raise RestoreError("GOOGLE_DRIVE_OAUTH_JSON is required")
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    paths = []
    for spec in parts:
        path = work / f"part{spec['index']:03d}"
        download_part(token, spec, path)
        paths.append(path)
        print(f"RESTORE_DRIVE_ARTIFACT: VERIFIED part={spec['index']} size={spec['size']} sha256={spec['sha256']}")
    output = Path(args.output)
    reconstruct(paths, output, int(logical["size"]), str(logical["sha256"]))
    print(f"RESTORE_DRIVE_ARTIFACT: PASS output={output} size={output.stat().st_size} sha256={sha256_file(output)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RestoreError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"RESTORE_DRIVE_ARTIFACT: FAIL: {exc}", file=sys.stderr)
        raise SystemExit(2)
