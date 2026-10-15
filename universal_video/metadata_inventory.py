"""Bounded, metadata-only capture of ONE exact Drive folder in an approved runtime.

    python -m universal_video.metadata_inventory --folder-id ID --private-dir DIR

DIR must already be a private, current-user-owned POSIX directory (0700).
The existing drive_adapter.access_token supplies credentials inside the worker;
this tool neither locates credentials nor changes OAuth scopes. Import is inert.
Only metadata GETs are issued: at most 6 requests, 500 direct children, 120s
including authentication/startup, and 8MiB of decoded response bodies. No retries,
redirects, recursion, downloads, queue operations, remote writes or execution.
Exhausting the byte budget before EOF is proven is BLOCKED, even at exact size.
After the collection deadline, killing/reaping the worker has a 2s cleanup wait.

stdout contains only a sanitized status, counts and digest. A COMPLETE capture
creates DIR/capture-*/snapshot.json privately; BLOCKED/ERROR captures do not
publish a snapshot. Raw responses (including names, IDs and pagination tokens)
stay private. Trashed objects, folders and shortcuts are retained, never followed
or silently excluded. Missing duration/checksums remain missing or null.

COMPLETE means pagination of the selected query, as visible to the existing
principal, ended with incompleteSearch=false on every page. This is NOT an
atomic remote snapshot or proof of archive-wide visibility. The raw capture is
not queue intake or batch_plan input: source selection is a separate review.
Windows private-directory ACL enforcement is unsupported and fails before auth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import stat
import tempfile
import time
from datetime import datetime
from pathlib import Path

MAX_GETS = 6
MAX_ITEMS = 500
MAX_SECONDS = 120
MAX_BYTES = 8 * 1024**2
PAGE_SIZE = 100
FOLDER_MIME = "application/vnd.google-apps.folder"
ID_RE = re.compile(r"[A-Za-z0-9_-]{10,200}")
TIME_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])"
)
FILE_FIELDS = (
    "id,name,mimeType,parents,size,version,modifiedTime,trashed,driveId,"
    "md5Checksum,sha1Checksum,sha256Checksum,videoMediaMetadata(durationMillis)"
)
COUNT_KEYS = ("get_requests", "pages_received", "items_received", "response_bytes",
              "video_items", "trashed_items")


class InventoryError(ValueError):
    """Only fixed codes, never remote content or exception strings, are public."""

    def __init__(self, code: str, *, blocked: bool = False):
        super().__init__(code)
        self.code = code
        self.status = "BLOCKED" if blocked else "ERROR"


def _receipt(status, code, counts=None, digest=None):
    return {"schema": "universal-video-metadata-receipt-v1", "status": status,
            "code": code, "counts": counts, "inventory_sha256": digest,
            "execution_allowed": False}


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError("INVALID_JSON")
        result[key] = value
    return result


def _reject_constant(_value):
    raise InventoryError("INVALID_JSON")


def _decode(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        _encode(value)  # Also rejects invalid Unicode and overflowed floats.
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InventoryError("INVALID_JSON") from exc


def _id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise InventoryError("INVALID_ID")
    return value


def _unsigned(value, *, positive=False):
    return (isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value)
            and int(positive) <= int(value) <= 2**64 - 1)


def _metadata(row, folder_id, *, folder=False):
    if not isinstance(row, dict):
        raise InventoryError("INVALID_METADATA")
    file_id = _id(row.get("id"))
    if folder:
        if file_id != folder_id or row.get("mimeType") != FOLDER_MIME:
            raise InventoryError("INVALID_FOLDER")
        if row.get("trashed") is not False:
            raise InventoryError("FOLDER_TRASH_STATE")
    elif file_id == folder_id or row.get("parents") != [folder_id]:
        raise InventoryError("WRONG_PARENT")
    if (not isinstance(row.get("name"), str) or not row["name"].strip()
            or not isinstance(row.get("mimeType"), str)
            or not re.fullmatch(r"[^/\s]+/[^/\s]+", row["mimeType"])
            or type(row.get("trashed")) is not bool):
        raise InventoryError("INVALID_METADATA")
    version, modified = row.get("version"), row.get("modifiedTime")
    if (not _unsigned(version, positive=True) or version.startswith("0")
            or not isinstance(modified, str) or not TIME_RE.fullmatch(modified)):
        raise InventoryError("UNRESOLVED_SOURCE_REVISION", blocked=True)
    try:
        datetime.fromisoformat(modified.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InventoryError("INVALID_SOURCE_REVISION") from exc
    for field, length in (("md5Checksum", 32), ("sha1Checksum", 40), ("sha256Checksum", 64)):
        checksum = row.get(field)
        if checksum is not None and (not isinstance(checksum, str)
                                    or not re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", checksum)):
            raise InventoryError("INVALID_CHECKSUM")
    if row.get("size") is not None and not _unsigned(row["size"]):
        raise InventoryError("INVALID_SIZE")
    media = row.get("videoMediaMetadata")
    if media is not None:
        if not isinstance(media, dict):
            raise InventoryError("INVALID_DURATION")
        duration = media.get("durationMillis")
        if duration is not None and not _unsigned(duration):
            raise InventoryError("INVALID_DURATION")
    if "driveId" in row:
        _id(row["driveId"])
    return file_id


def _collect_metadata(folder_id, *, token_provider, session, base_url,
                      counts, clock=time.monotonic):
    """Private core; injectable clients allow tests with no credentials/network."""
    _id(folder_id)
    deadline = clock() + MAX_SECONDS

    def remaining():
        value = deadline - clock()
        if value <= 0:
            raise InventoryError("TIME_LIMIT", blocked=True)
        return value

    remaining()
    token = token_provider()
    remaining()
    if not isinstance(token, str) or not token:
        raise InventoryError("AUTH_FAILED")

    def get(path, params):
        remaining()
        if counts["get_requests"] >= MAX_GETS:
            raise InventoryError("GET_LIMIT", blocked=True)
        counts["get_requests"] += 1
        with session.get(base_url + path, params=params, stream=True,
                         headers={"Authorization": "Bearer " + token},
                         allow_redirects=False, timeout=min(30, remaining())) as response:
            remaining()
            if response.status_code != 200:
                raise InventoryError("HTTP_ERROR")
            chunks = []
            while True:
                budget = MAX_BYTES - counts["response_bytes"]
                if budget <= 0:
                    raise InventoryError("BYTE_LIMIT", blocked=True)
                chunk = response.raw.read(min(64 * 1024, budget), decode_content=True)
                remaining()
                counts["response_bytes"] += len(chunk)
                if len(chunk) > budget:
                    raise InventoryError("BYTE_LIMIT", blocked=True)
                if not chunk:
                    break
                chunks.append(chunk)
            remaining()
        payload = _decode(b"".join(chunks))
        if not isinstance(payload, dict):
            raise InventoryError("INVALID_METADATA")
        return payload

    folder = get("/files/" + folder_id,
                 {"fields": FILE_FIELDS, "supportsAllDrives": "true"})
    _metadata(folder, folder_id, folder=True)
    params = {"q": f"'{folder_id}' in parents", "spaces": "drive",
              "pageSize": PAGE_SIZE, "supportsAllDrives": "true",
              "includeItemsFromAllDrives": "true", "corpora": "user",
              "fields": f"nextPageToken,incompleteSearch,files({FILE_FIELDS})"}
    if "driveId" in folder:
        params.update(corpora="drive", driveId=folder["driveId"])
    pages, files, seen, tokens = [], [], set(), set()
    while True:
        page = get("/files", params.copy())
        counts["pages_received"] += 1
        if page.get("incompleteSearch") is not False:
            raise InventoryError("UNKNOWN_COMPLETENESS", blocked=True)
        rows = page.get("files")
        if not isinstance(rows, list):
            raise InventoryError("INVALID_METADATA")
        counts["items_received"] += len(rows)
        if counts["items_received"] > MAX_ITEMS:
            raise InventoryError("ITEM_LIMIT", blocked=True)
        if len(rows) > PAGE_SIZE:
            raise InventoryError("INVALID_PAGE_SIZE")
        for row in rows:
            file_id = _metadata(row, folder_id)
            if file_id in seen:
                raise InventoryError("DUPLICATE_ID")
            seen.add(file_id)
            files.append(row)
            counts["video_items"] += int(row["mimeType"].startswith("video/"))
            counts["trashed_items"] += int(row["trashed"])
        pages.append(page)
        next_token = page.get("nextPageToken")
        if next_token is None or next_token == "":
            break
        if not isinstance(next_token, str) or not next_token.strip():
            raise InventoryError("INVALID_PAGE_TOKEN")
        if next_token in tokens:
            raise InventoryError("REPEATED_PAGE_TOKEN")
        tokens.add(next_token)
        if counts["items_received"] >= MAX_ITEMS:
            raise InventoryError("ITEM_LIMIT", blocked=True)
        params["pageToken"] = next_token
    remaining()
    inventory = {"folder": folder, "files": sorted(files, key=lambda row: row["id"])}
    digest = hashlib.sha256(_encode(inventory)).hexdigest()
    raw = {"schema": "universal-video-raw-metadata-v1", "folder": folder,
           "pages": pages, "inventory_sha256": digest, "execution_allowed": False}
    return raw, _receipt("COMPLETE", "OK", counts, digest)


def _private_directory(path):
    # POSIX permissions cannot establish the privacy of a Windows ACL.
    if os.name != "posix":
        raise InventoryError("UNSUPPORTED_PRIVATE_RUNTIME", blocked=True)
    path = Path(os.path.abspath(path))
    uid = os.getuid()
    for entry in (path, *path.parents):
        info = entry.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid):
            raise InventoryError("UNSAFE_PRIVATE_DIRECTORY", blocked=True)
        if entry == path:
            if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
                raise InventoryError("UNSAFE_PRIVATE_DIRECTORY", blocked=True)
        elif info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise InventoryError("UNSAFE_PRIVATE_DIRECTORY", blocked=True)
    return path


def _worker(folder_id, staging_path, sender):
    # Suppress dependency logs/tracebacks at OS descriptor level before importing
    # the existing credential provider. No exception text crosses this boundary.
    try:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), 1)
            os.dup2(sink.fileno(), 2)
        counts = dict.fromkeys(COUNT_KEYS, 0)
        try:
            from . import drive_adapter

            with drive_adapter.requests.Session() as session:
                # Avoid implicit .netrc credentials; OAuth is supplied explicitly.
                session.trust_env = False
                raw, result = _collect_metadata(
                    folder_id, token_provider=drive_adapter.access_token,
                    session=session, base_url=drive_adapter.DRIVE, counts=counts)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            with os.fdopen(os.open(staging_path, flags, 0o600), "wb") as handle:
                handle.write(_encode(raw))
        except InventoryError as exc:
            result = _receipt(exc.status, exc.code, counts)
        except BaseException:
            result = _receipt("ERROR", "CAPTURE_FAILED", counts)
        encoded = _encode(result)
        if len(encoded) <= 2048:
            sender.send_bytes(encoded)
    except BaseException:
        pass  # Parent reports WORKER_FAILED without a dependency traceback.
    finally:
        sender.close()


def _validate_receipt(value):
    if (not isinstance(value, dict) or set(value) != set(_receipt("", ""))
            or value["schema"] != "universal-video-metadata-receipt-v1"
            or value["execution_allowed"] is not False
            or value["status"] not in {"COMPLETE", "BLOCKED", "ERROR"}
            or not isinstance(value["code"], str)
            or not re.fullmatch(r"[A-Z_]{2,50}", value["code"])
            or not isinstance(value["counts"], dict)
            or set(value["counts"]) != set(COUNT_KEYS)
            or any(type(n) is not int or not 0 <= n <= MAX_BYTES * 2
                   for n in value["counts"].values())):
        raise InventoryError("WORKER_FAILED")
    digest = value["inventory_sha256"]
    if value["status"] == "COMPLETE":
        if value["code"] != "OK" or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise InventoryError("WORKER_FAILED")
    elif digest is not None:
        raise InventoryError("WORKER_FAILED")
    return value


def collect_inventory(folder_id, private_dir):
    """Run one bounded worker; no live action until explicit caller invocation."""
    deadline = time.monotonic() + MAX_SECONDS
    capture = staging = process = receiver = sender = None
    published = False
    try:
        _id(folder_id)
        directory = _private_directory(private_dir)
        capture = Path(tempfile.mkdtemp(prefix="capture-", dir=directory))
        staging = capture / "snapshot.partial"
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(folder_id, str(staging), sender))
        if time.monotonic() >= deadline:
            raise InventoryError("TIME_LIMIT", blocked=True)
        process.start()
        sender.close()
        process.join(max(0, deadline - time.monotonic()))
        if process.is_alive() or time.monotonic() >= deadline:
            raise InventoryError("TIME_LIMIT", blocked=True)
        if process.exitcode != 0 or not receiver.poll():
            raise InventoryError("WORKER_FAILED")
        result = _validate_receipt(_decode(receiver.recv_bytes(maxlength=2048)))
        if result["status"] == "COMPLETE":
            info = staging.lstat()
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise InventoryError("UNSAFE_PRIVATE_FILE", blocked=True)
            if time.monotonic() >= deadline:
                raise InventoryError("TIME_LIMIT", blocked=True)
            # Atomic publication without overwriting any existing file/symlink.
            os.link(staging, capture / "snapshot.json", follow_symlinks=False)
            published = True
    except InventoryError as exc:
        result = _receipt(exc.status, exc.code)
    except Exception:
        result = _receipt("ERROR", "CAPTURE_FAILED")
    except KeyboardInterrupt:
        result = _receipt("ERROR", "INTERRUPTED")
    finally:
        # Also contain a child on parent exceptions or caller interruption.
        cleanup_failed = False
        try:
            if process is not None and process.pid is not None:
                if process.is_alive():
                    process.kill()
                process.join(timeout=2)
                process.close()
        except (OSError, ValueError):
            cleanup_failed = True
        for channel in (receiver, sender):
            if channel is not None:
                try:
                    channel.close()
                except OSError:
                    cleanup_failed = True
        try:
            if staging is not None:
                staging.unlink(missing_ok=True)
            if capture is not None and not published:
                capture.rmdir()
        except OSError:
            cleanup_failed = True
    return _receipt("ERROR", "CLEANUP_FAILED") if cleanup_failed else result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder-id", required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    result = collect_inventory(args.folder_id, args.private_dir)
    print(_encode(result).decode("utf-8"))
    return {"COMPLETE": 0, "BLOCKED": 2, "ERROR": 1}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
