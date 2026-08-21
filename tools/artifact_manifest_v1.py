#!/usr/bin/env python3
"""Create and verify reproducible manifests for school-derived artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "bridge-artifact-manifest-v1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def file_entry(role: str, path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(p)
    mime, _ = mimetypes.guess_type(p.name)
    return {
        "role": role,
        "name": p.name,
        "local_path": p.as_posix(),
        "size_bytes": p.stat().st_size,
        "sha256": sha256_file(p),
        "mime_type": mime or "application/octet-stream",
        "immutable_source": False,
    }


def normalize_external_entry(entry: dict[str, Any]) -> dict[str, Any]:
    required = {"role", "name", "size_bytes", "sha256", "locator"}
    missing = sorted(required - set(entry))
    if missing:
        raise ValueError(f"external artifact entry missing fields: {missing}")
    sha = str(entry["sha256"]).lower()
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha):
        raise ValueError(f"invalid sha256 for {entry['name']}")
    return {
        "role": str(entry["role"]),
        "name": str(entry["name"]),
        "locator": str(entry["locator"]),
        "size_bytes": int(entry["size_bytes"]),
        "sha256": sha,
        "mime_type": str(entry.get("mime_type") or "application/octet-stream"),
        "immutable_source": bool(entry.get("immutable_source", True)),
        "metadata": dict(entry.get("metadata") or {}),
    }


def build_manifest(
    local_entries: list[dict[str, Any]],
    external_entries: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entries = list(local_entries) + [normalize_external_entry(x) for x in external_entries]
    if not entries:
        raise ValueError("manifest must contain at least one artifact")
    identity = {
        "schema": SCHEMA,
        "entries": entries,
        "metadata": dict(metadata or {}),
    }
    return {
        **identity,
        "manifest_id": canonical_digest(identity),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def verify_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported manifest schema")
    identity = {
        "schema": manifest["schema"],
        "entries": manifest.get("entries") or [],
        "metadata": manifest.get("metadata") or {},
    }
    expected_id = canonical_digest(identity)
    errors: list[dict[str, Any]] = []
    checked_local = 0
    for entry in identity["entries"]:
        local_path = entry.get("local_path")
        if not local_path:
            continue
        checked_local += 1
        p = Path(local_path)
        if not p.exists():
            errors.append({"name": entry.get("name"), "error": "LOCAL_FILE_MISSING"})
            continue
        actual_size = p.stat().st_size
        actual_sha = sha256_file(p)
        if actual_size != int(entry.get("size_bytes", -1)):
            errors.append({"name": entry.get("name"), "error": "SIZE_MISMATCH", "actual": actual_size})
        if actual_sha != str(entry.get("sha256", "")).lower():
            errors.append({"name": entry.get("name"), "error": "SHA256_MISMATCH", "actual": actual_sha})
    if expected_id != manifest.get("manifest_id"):
        errors.append({"error": "MANIFEST_ID_MISMATCH", "actual": expected_id})
    return {
        "status": "PASS" if not errors else "FAIL",
        "manifest_id": expected_id,
        "entry_count": len(identity["entries"]),
        "local_files_checked": checked_local,
        "errors": errors,
    }


def _load_json_arg(value: str | None, default: Any) -> Any:
    if not value:
        return default
    candidate = Path(value)
    if candidate.is_file():
        return json.loads(candidate.read_text(encoding="utf-8"))
    return json.loads(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", action="append", default=[], help="ROLE=PATH; repeatable")
    parser.add_argument("--external-json", help="JSON file or inline JSON array of external entries")
    parser.add_argument("--metadata-json", help="JSON file or inline JSON object")
    parser.add_argument("--output")
    parser.add_argument("--verify")
    args = parser.parse_args()

    if args.verify:
        manifest = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        result = verify_manifest(manifest)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        raise SystemExit(0 if result["status"] == "PASS" else 2)

    local_entries: list[dict[str, Any]] = []
    for spec in args.file:
        if "=" not in spec:
            raise SystemExit("--file must be ROLE=PATH")
        role, path = spec.split("=", 1)
        local_entries.append(file_entry(role, path))
    external_entries = _load_json_arg(args.external_json, [])
    metadata = _load_json_arg(args.metadata_json, {})
    if not isinstance(external_entries, list):
        raise SystemExit("--external-json must resolve to an array")
    if not isinstance(metadata, dict):
        raise SystemExit("--metadata-json must resolve to an object")
    manifest = build_manifest(local_entries, external_entries, metadata)
    text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
