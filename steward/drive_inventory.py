"""Read-only recursive Google Drive inventory for the bridge school.

The collector is deliberately conservative:
- it uses Drive ``files.list`` GET requests only;
- every page and item is validated;
- repeated page tokens and ambiguous folder references fail closed;
- a manifest is written only after a complete traversal;
- source files are never moved, renamed, copied, shared, or deleted.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Sequence

import requests

DRIVE_API_FILES_URL = "https://www.googleapis.com/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
DEFAULT_ROOT_FOLDER_ID = "1uzpcT49YcV34gcv_Jo2U86a9qHUuqPpj"
DEFAULT_ROOT_NAME = "Школа спортивного бриджа"
INVENTORY_SCHEMA_VERSION = "school-systems-drive-inventory-v1"
VIDEO_EXTENSIONS = {
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".mts",
    ".ts",
    ".webm",
    ".wmv",
}
_DRIVE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class DriveInventoryError(RuntimeError):
    """Bounded failure raised when a complete inventory cannot be proven."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


TokenProvider = Callable[[], str]


@dataclass(frozen=True)
class TraversalTarget:
    folder_id: str
    path: tuple[str, ...]
    parent_id: str | None
    via_shortcut_id: str | None = None


class DriveListClient:
    """Minimal read-only Drive v3 client with explicit pagination checks."""

    def __init__(
        self,
        *,
        token_provider: TokenProvider,
        session: requests.Session | Any | None = None,
        timeout_seconds: float = 30.0,
        page_size: int = 1000,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if not 1 <= page_size <= 1000:
            raise ValueError("page_size must be in [1,1000]")
        self._token_provider = token_provider
        self._session = session or requests.Session()
        self._timeout_seconds = float(timeout_seconds)
        self._page_size = int(page_size)

    @staticmethod
    def _validate_folder_id(folder_id: str) -> str:
        value = str(folder_id or "").strip()
        if not _DRIVE_ID_RE.fullmatch(value):
            raise DriveInventoryError("INVALID_FOLDER_ID")
        return value

    def list_children(self, folder_id: str) -> list[dict[str, Any]]:
        """List every direct child, proving pagination is complete."""
        folder_id = self._validate_folder_id(folder_id)
        try:
            token = str(self._token_provider() or "").strip()
        except Exception as exc:
            raise DriveInventoryError("DRIVE_TOKEN_UNAVAILABLE") from exc
        if not token:
            raise DriveInventoryError("DRIVE_TOKEN_UNAVAILABLE")

        headers = {"Authorization": f"Bearer {token}"}
        page_token: str | None = None
        seen_page_tokens: set[str] = set()
        items: list[dict[str, Any]] = []

        while True:
            params: dict[str, Any] = {
                "q": f"'{folder_id}' in parents and trashed = false",
                "fields": (
                    "nextPageToken,files("
                    "id,name,mimeType,size,md5Checksum,createdTime,modifiedTime,"
                    "parents,videoMediaMetadata,shortcutDetails)"
                ),
                "pageSize": self._page_size,
                "spaces": "drive",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if page_token:
                params["pageToken"] = page_token
            try:
                response = self._session.get(
                    DRIVE_API_FILES_URL,
                    headers=headers,
                    params=params,
                    timeout=self._timeout_seconds,
                )
            except Exception as exc:
                raise DriveInventoryError("DRIVE_LIST_REQUEST_FAILED") from exc
            status_code = int(getattr(response, "status_code", 0) or 0)
            if not 200 <= status_code < 300:
                raise DriveInventoryError(f"DRIVE_LIST_HTTP_{status_code or 'UNKNOWN'}")
            try:
                payload = response.json()
            except Exception as exc:
                raise DriveInventoryError("DRIVE_LIST_INVALID_JSON") from exc
            if not isinstance(payload, Mapping):
                raise DriveInventoryError("DRIVE_LIST_INVALID_PAYLOAD")
            page_items = payload.get("files")
            if not isinstance(page_items, list):
                raise DriveInventoryError("DRIVE_LIST_FILES_NOT_ARRAY")
            for raw in page_items:
                if not isinstance(raw, Mapping):
                    raise DriveInventoryError("DRIVE_LIST_ITEM_NOT_OBJECT")
                items.append(dict(raw))

            next_token_raw = payload.get("nextPageToken")
            if next_token_raw in (None, ""):
                break
            next_token = str(next_token_raw)
            if next_token in seen_page_tokens or next_token == page_token:
                raise DriveInventoryError("DRIVE_LIST_REPEATED_PAGE_TOKEN")
            seen_page_tokens.add(next_token)
            page_token = next_token

        items.sort(
            key=lambda item: (
                str(item.get("name") or "").casefold(),
                str(item.get("id") or ""),
            )
        )
        return items


def _default_token_provider() -> str:
    # Reuse the already bounded Universal Video OAuth loader. Import lazily so
    # unit tests do not require credentials or google-auth initialization.
    from universal_video.drive_adapter import _access_token  # type: ignore

    return str(_access_token())


def _safe_int(value: Any, *, code: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise DriveInventoryError(code) from exc
    if result < 0:
        raise DriveInventoryError(code)
    return result


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^(copy of|копия)\s+", "", text)
    text = re.sub(r"\s*\(\d+\)(?=\.[^.]+$)", "", text)
    suffixes = Path(text).suffixes
    if len(suffixes) >= 2 and suffixes[-1] == suffixes[-2]:
        text = text[: -len(suffixes[-1])]
    return text


def _video_detection(name: str, mime_type: str) -> tuple[bool, str | None]:
    if mime_type.startswith("video/"):
        return True, "mime"
    suffix = Path(name).suffix.casefold()
    if suffix in VIDEO_EXTENSIONS:
        return True, "extension"
    return False, None


def _validate_item(raw: Mapping[str, Any], expected_parent_id: str) -> dict[str, Any]:
    item_id = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    mime_type = str(raw.get("mimeType") or "").strip()
    if not _DRIVE_ID_RE.fullmatch(item_id):
        raise DriveInventoryError("DRIVE_ITEM_INVALID_ID")
    if not name:
        raise DriveInventoryError("DRIVE_ITEM_EMPTY_NAME")
    if not mime_type:
        raise DriveInventoryError("DRIVE_ITEM_EMPTY_MIME")
    parents_raw = raw.get("parents")
    if parents_raw is not None:
        if not isinstance(parents_raw, list) or not all(
            isinstance(parent, str) for parent in parents_raw
        ):
            raise DriveInventoryError("DRIVE_ITEM_INVALID_PARENTS")
        if expected_parent_id not in parents_raw:
            raise DriveInventoryError("DRIVE_ITEM_PARENT_MISMATCH")
    size = _safe_int(raw.get("size"), code="DRIVE_ITEM_INVALID_SIZE")
    checksum_raw = raw.get("md5Checksum")
    checksum = None if checksum_raw in (None, "") else str(checksum_raw).lower()
    if checksum is not None and not re.fullmatch(r"[0-9a-f]{32}", checksum):
        raise DriveInventoryError("DRIVE_ITEM_INVALID_MD5")
    shortcut_details_raw = raw.get("shortcutDetails")
    shortcut_details: dict[str, str] | None = None
    if shortcut_details_raw is not None:
        if not isinstance(shortcut_details_raw, Mapping):
            raise DriveInventoryError("DRIVE_ITEM_INVALID_SHORTCUT")
        target_id = str(shortcut_details_raw.get("targetId") or "").strip()
        target_mime = str(shortcut_details_raw.get("targetMimeType") or "").strip()
        if not _DRIVE_ID_RE.fullmatch(target_id) or not target_mime:
            raise DriveInventoryError("DRIVE_ITEM_INVALID_SHORTCUT")
        shortcut_details = {
            "target_id": target_id,
            "target_mime_type": target_mime,
        }
    video_metadata_raw = raw.get("videoMediaMetadata")
    video_metadata: dict[str, int] | None = None
    if video_metadata_raw is not None:
        if not isinstance(video_metadata_raw, Mapping):
            raise DriveInventoryError("DRIVE_ITEM_INVALID_VIDEO_METADATA")
        video_metadata = {}
        for source_key, target_key in (
            ("durationMillis", "duration_millis"),
            ("width", "width"),
            ("height", "height"),
        ):
            parsed = _safe_int(
                video_metadata_raw.get(source_key),
                code="DRIVE_ITEM_INVALID_VIDEO_METADATA",
            )
            if parsed is not None:
                video_metadata[target_key] = parsed
    return {
        "id": item_id,
        "name": name,
        "mime_type": mime_type,
        "size": size,
        "md5_checksum": checksum,
        "created_time": raw.get("createdTime"),
        "modified_time": raw.get("modifiedTime"),
        "shortcut_details": shortcut_details,
        "video_metadata": video_metadata,
    }


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("path") or "").casefold(),
        str(record.get("id") or ""),
    )


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_drive_inventory(
    client: DriveListClient,
    *,
    root_folder_id: str = DEFAULT_ROOT_FOLDER_ID,
    root_name: str = DEFAULT_ROOT_NAME,
) -> dict[str, Any]:
    """Recursively inventory a Drive tree and return a complete manifest."""
    root_folder_id = DriveListClient._validate_folder_id(root_folder_id)
    root_name = str(root_name or "").strip()
    if not root_name:
        raise ValueError("root_name must be non-empty")

    queue: deque[TraversalTarget] = deque(
        [TraversalTarget(folder_id=root_folder_id, path=(root_name,), parent_id=None)]
    )
    scheduled: dict[str, tuple[str, ...]] = {root_folder_id: (root_name,)}
    visited: set[str] = set()
    folder_records: dict[str, dict[str, Any]] = {
        root_folder_id: {
            "id": root_folder_id,
            "name": root_name,
            "path": root_name,
            "parent_id": None,
            "via_shortcut_id": None,
            "alias_paths": [],
            "direct_file_count": 0,
            "direct_folder_count": 0,
            "direct_size_bytes": 0,
            "recursive_file_count": 0,
            "recursive_folder_count": 0,
            "recursive_size_bytes": 0,
        }
    }
    files: list[dict[str, Any]] = []
    videos: list[dict[str, Any]] = []
    seen_file_ids: dict[str, str] = {}
    shortcut_records: list[dict[str, Any]] = []

    while queue:
        target = queue.popleft()
        if target.folder_id in visited:
            raise DriveInventoryError("DRIVE_FOLDER_TRAVERSED_TWICE")
        visited.add(target.folder_id)
        children = client.list_children(target.folder_id)
        folder_record = folder_records[target.folder_id]
        folder_record["direct_child_count"] = len(children)

        for raw in children:
            item = _validate_item(raw, target.folder_id)
            item_path_tuple = target.path + (item["name"],)
            item_path = "/".join(item_path_tuple)
            mime_type = item["mime_type"]

            if mime_type == FOLDER_MIME:
                child_id = item["id"]
                existing_path = scheduled.get(child_id)
                if existing_path is not None:
                    raise DriveInventoryError("DRIVE_DUPLICATE_FOLDER_REFERENCE")
                scheduled[child_id] = item_path_tuple
                folder_records[child_id] = {
                    "id": child_id,
                    "name": item["name"],
                    "path": item_path,
                    "parent_id": target.folder_id,
                    "via_shortcut_id": None,
                    "alias_paths": [],
                    "direct_file_count": 0,
                    "direct_folder_count": 0,
                    "direct_size_bytes": 0,
                    "recursive_file_count": 0,
                    "recursive_folder_count": 0,
                    "recursive_size_bytes": 0,
                }
                folder_record["direct_folder_count"] += 1
                queue.append(
                    TraversalTarget(child_id, item_path_tuple, target.folder_id)
                )
                continue

            if mime_type == SHORTCUT_MIME:
                details = item["shortcut_details"]
                if details is None:
                    raise DriveInventoryError("DRIVE_SHORTCUT_DETAILS_MISSING")
                shortcut_record = {
                    "id": item["id"],
                    "name": item["name"],
                    "path": item_path,
                    "parent_id": target.folder_id,
                    "target_id": details["target_id"],
                    "target_mime_type": details["target_mime_type"],
                }
                shortcut_records.append(shortcut_record)
                if details["target_mime_type"] == FOLDER_MIME:
                    child_id = details["target_id"]
                    existing_path = scheduled.get(child_id)
                    if existing_path is not None:
                        folder_records[child_id]["alias_paths"].append(item_path)
                        continue
                    scheduled[child_id] = item_path_tuple
                    folder_records[child_id] = {
                        "id": child_id,
                        "name": item["name"],
                        "path": item_path,
                        "parent_id": target.folder_id,
                        "via_shortcut_id": item["id"],
                        "alias_paths": [],
                        "direct_file_count": 0,
                        "direct_folder_count": 0,
                        "direct_size_bytes": 0,
                        "recursive_file_count": 0,
                        "recursive_folder_count": 0,
                        "recursive_size_bytes": 0,
                    }
                    folder_record["direct_folder_count"] += 1
                    queue.append(
                        TraversalTarget(
                            child_id,
                            item_path_tuple,
                            target.folder_id,
                            item["id"],
                        )
                    )
                else:
                    is_video, basis = _video_detection(
                        item["name"], details["target_mime_type"]
                    )
                    record = {
                        "id": item["id"],
                        "name": item["name"],
                        "path": item_path,
                        "parent_id": target.folder_id,
                        "mime_type": mime_type,
                        "size_bytes": item["size"],
                        "md5_checksum": item["md5_checksum"],
                        "created_time": item["created_time"],
                        "modified_time": item["modified_time"],
                        "is_video": is_video,
                        "video_detection_basis": basis,
                        "video_metadata": item["video_metadata"],
                        "is_shortcut": True,
                        "shortcut_target_id": details["target_id"],
                        "shortcut_target_mime_type": details["target_mime_type"],
                    }
                    files.append(record)
                    if is_video:
                        videos.append(dict(record))
                    folder_record["direct_file_count"] += 1
                    folder_record["direct_size_bytes"] += item["size"] or 0
                continue

            previous_path = seen_file_ids.get(item["id"])
            if previous_path is not None:
                raise DriveInventoryError("DRIVE_DUPLICATE_FILE_REFERENCE")
            seen_file_ids[item["id"]] = item_path
            is_video, basis = _video_detection(item["name"], mime_type)
            record = {
                "id": item["id"],
                "name": item["name"],
                "path": item_path,
                "parent_id": target.folder_id,
                "mime_type": mime_type,
                "size_bytes": item["size"],
                "md5_checksum": item["md5_checksum"],
                "created_time": item["created_time"],
                "modified_time": item["modified_time"],
                "is_video": is_video,
                "video_detection_basis": basis,
                "video_metadata": item["video_metadata"],
                "is_shortcut": False,
                "shortcut_target_id": None,
                "shortcut_target_mime_type": None,
            }
            files.append(record)
            if is_video:
                videos.append(dict(record))
            folder_record["direct_file_count"] += 1
            folder_record["direct_size_bytes"] += item["size"] or 0

    ordered_folders = sorted(
        folder_records.values(),
        key=lambda record: record["path"].count("/"),
        reverse=True,
    )
    for record in ordered_folders:
        record["recursive_file_count"] += record["direct_file_count"]
        record["recursive_folder_count"] += record["direct_folder_count"]
        record["recursive_size_bytes"] += record["direct_size_bytes"]
        parent_id = record["parent_id"]
        if parent_id is not None:
            parent = folder_records.get(parent_id)
            if parent is None:
                raise DriveInventoryError("DRIVE_FOLDER_PARENT_MISSING")
            parent["recursive_file_count"] += record["recursive_file_count"]
            parent["recursive_folder_count"] += record[
                "recursive_folder_count"
            ]
            parent["recursive_size_bytes"] += record["recursive_size_bytes"]

    folders = sorted(folder_records.values(), key=_record_sort_key)
    files.sort(key=_record_sort_key)
    videos.sort(key=_record_sort_key)
    shortcut_records.sort(key=_record_sort_key)

    exact_groups: list[dict[str, Any]] = []
    by_md5: MutableMapping[str, list[dict[str, Any]]] = defaultdict(list)
    for record in files:
        checksum = record.get("md5_checksum")
        if checksum:
            by_md5[str(checksum)].append(record)
    for checksum, members in sorted(by_md5.items()):
        if len(members) > 1:
            exact_groups.append(
                {
                    "md5_checksum": checksum,
                    "file_ids": [member["id"] for member in members],
                    "paths": [member["path"] for member in members],
                    "size_bytes": members[0].get("size_bytes"),
                }
            )

    probable_groups: list[dict[str, Any]] = []
    by_name_size: MutableMapping[
        tuple[str, int | None], list[dict[str, Any]]
    ] = defaultdict(list)
    for record in files:
        by_name_size[
            (_normalize_name(record["name"]), record.get("size_bytes"))
        ].append(record)
    for (normalized_name, size), members in sorted(by_name_size.items()):
        if len(members) > 1:
            checksums = {
                member.get("md5_checksum")
                for member in members
                if member.get("md5_checksum")
            }
            if len(checksums) == 1 and checksums:
                continue
            probable_groups.append(
                {
                    "normalized_name": normalized_name,
                    "size_bytes": size,
                    "file_ids": [member["id"] for member in members],
                    "paths": [member["path"] for member in members],
                    "classification": "PROBABLE_ONLY",
                }
            )

    direct_empty_folders = [
        record["path"]
        for record in folders
        if record.get("direct_child_count", 0) == 0
    ]
    subtree_empty_folders = [
        record["path"]
        for record in folders
        if record["recursive_file_count"] == 0
    ]
    extension_detected_video_count = sum(
        1
        for video in videos
        if video["video_detection_basis"] == "extension"
    )

    stable_payload: dict[str, Any] = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "status": "COMPLETE",
        "root": {"folder_id": root_folder_id, "name": root_name},
        "coverage": {
            "recursive": True,
            "pagination_proven": True,
            "trashed_included": False,
            "shortcut_folders_followed": True,
            "unique_physical_folders": len(folders),
        },
        "counts": {
            "folders": len(folders),
            "files": len(files),
            "videos": len(videos),
            "shortcuts": len(shortcut_records),
            "extension_detected_videos": extension_detected_video_count,
            "exact_duplicate_groups": len(exact_groups),
            "probable_duplicate_groups": len(probable_groups),
            "direct_empty_folders": len(direct_empty_folders),
            "subtree_empty_folders": len(subtree_empty_folders),
            "total_file_size_bytes": sum(
                record.get("size_bytes") or 0 for record in files
            ),
            "total_video_size_bytes": sum(
                record.get("size_bytes") or 0 for record in videos
            ),
        },
        "folders": folders,
        "files": files,
        "videos": videos,
        "shortcuts": shortcut_records,
        "duplicates": {"exact": exact_groups, "probable": probable_groups},
        "empty_folders": {
            "direct": sorted(direct_empty_folders, key=str.casefold),
            "subtree": sorted(subtree_empty_folders, key=str.casefold),
        },
    }
    manifest = dict(stable_payload)
    manifest["inventory_sha256"] = _canonical_sha256(stable_payload)
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    return manifest


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def write_inventory_outputs(
    manifest: Mapping[str, Any], output_dir: Path
) -> dict[str, Path]:
    """Atomically write the complete JSON, video CSV, and bounded summary."""
    if manifest.get("status") != "COMPLETE":
        raise DriveInventoryError("REFUSE_TO_WRITE_NON_COMPLETE_MANIFEST")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "drive-inventory.json"
    video_csv_path = output_dir / "drive-videos.csv"
    summary_path = output_dir / "drive-inventory-summary.txt"

    _atomic_write_text(
        manifest_path,
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )

    csv_temp = output_dir / ".drive-videos.csv.render"
    try:
        with csv_temp.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "id",
                    "name",
                    "path",
                    "parent_id",
                    "mime_type",
                    "size_bytes",
                    "md5_checksum",
                    "duration_millis",
                    "width",
                    "height",
                    "video_detection_basis",
                    "is_shortcut",
                    "shortcut_target_id",
                ],
            )
            writer.writeheader()
            for video in manifest.get("videos", []):
                metadata = video.get("video_metadata") or {}
                writer.writerow(
                    {
                        "id": video.get("id"),
                        "name": video.get("name"),
                        "path": video.get("path"),
                        "parent_id": video.get("parent_id"),
                        "mime_type": video.get("mime_type"),
                        "size_bytes": video.get("size_bytes"),
                        "md5_checksum": video.get("md5_checksum"),
                        "duration_millis": metadata.get("duration_millis"),
                        "width": metadata.get("width"),
                        "height": metadata.get("height"),
                        "video_detection_basis": video.get(
                            "video_detection_basis"
                        ),
                        "is_shortcut": video.get("is_shortcut"),
                        "shortcut_target_id": video.get(
                            "shortcut_target_id"
                        ),
                    }
                )
        _atomic_write_text(
            video_csv_path,
            csv_temp.read_text(encoding="utf-8"),
        )
    finally:
        try:
            csv_temp.unlink()
        except FileNotFoundError:
            pass

    counts = manifest.get("counts") or {}
    summary_lines = [
        f"schema_version={manifest.get('schema_version')}",
        f"status={manifest.get('status')}",
        f"root_folder_id={(manifest.get('root') or {}).get('folder_id')}",
        f"inventory_sha256={manifest.get('inventory_sha256')}",
        f"folders={counts.get('folders', 0)}",
        f"files={counts.get('files', 0)}",
        f"videos={counts.get('videos', 0)}",
        f"shortcuts={counts.get('shortcuts', 0)}",
        f"exact_duplicate_groups={counts.get('exact_duplicate_groups', 0)}",
        f"probable_duplicate_groups={counts.get('probable_duplicate_groups', 0)}",
        f"direct_empty_folders={counts.get('direct_empty_folders', 0)}",
        f"subtree_empty_folders={counts.get('subtree_empty_folders', 0)}",
        f"total_file_size_bytes={counts.get('total_file_size_bytes', 0)}",
        f"total_video_size_bytes={counts.get('total_video_size_bytes', 0)}",
    ]
    _atomic_write_text(summary_path, "\n".join(summary_lines) + "\n")
    return {
        "manifest": manifest_path,
        "videos_csv": video_csv_path,
        "summary": summary_path,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only recursive Drive inventory"
    )
    parser.add_argument(
        "--root-folder-id",
        default=DEFAULT_ROOT_FOLDER_ID,
    )
    parser.add_argument("--root-name", default=DEFAULT_ROOT_NAME)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = DriveListClient(
            token_provider=_default_token_provider,
            timeout_seconds=args.timeout_seconds,
        )
        manifest = build_drive_inventory(
            client,
            root_folder_id=args.root_folder_id,
            root_name=args.root_name,
        )
        write_inventory_outputs(manifest, args.output_dir)
    except DriveInventoryError as exc:
        print(f"SCHOOL_STEWARD_DRIVE_AUDIT_FAIL code={exc.code}")
        return 2
    except Exception:
        print("SCHOOL_STEWARD_DRIVE_AUDIT_FAIL code=UNEXPECTED")
        return 3
    print(
        "SCHOOL_STEWARD_DRIVE_AUDIT_PASS "
        f"manifest={args.output_dir / 'drive-inventory.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
