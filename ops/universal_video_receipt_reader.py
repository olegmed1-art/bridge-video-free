#!/usr/bin/env python3
"""Fail-closed reader for fixed Universal Video spool receipts.

The exact operator invokes this program as the unprivileged worker identity.
It pins one no-follow file descriptor, validates its metadata, and parses only
the bytes read from that descriptor so a worker-writable receipt cannot redirect
a privileged reader through a symlink or special file.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from typing import Any


MAX_RECEIPT_BYTES = 8 * 1024 * 1024
ERROR_TYPE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,119}$")
ERROR_CODE_RE = re.compile(r"^UV_[A-Z0-9_]{1,96}$")


class ReceiptError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReceiptError("duplicate JSON member")
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> None:
    raise ReceiptError("non-finite JSON number")


def _metadata_signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def safe_load(path: str) -> dict[str, Any]:
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReceiptError("receipt is not a regular file")
        if before.st_uid != os.geteuid() or before.st_gid != os.getegid():
            raise ReceiptError("receipt identity mismatch")
        if stat.S_IMODE(before.st_mode) != 0o640:
            raise ReceiptError("receipt mode mismatch")
        if before.st_size <= 0 or before.st_size > MAX_RECEIPT_BYTES:
            raise ReceiptError("receipt size outside bounded contract")

        remaining = before.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                raise ReceiptError("receipt changed while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ReceiptError("receipt grew while reading")
        after = os.fstat(descriptor)
        if _metadata_signature(after) != _metadata_signature(before):
            raise ReceiptError("receipt metadata changed while reading")
    finally:
        os.close(descriptor)

    try:
        text = b"".join(chunks).decode("utf-8", errors="strict")
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("receipt is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ReceiptError("receipt root must be an object")
    return payload


def _verify_identity(
    payload: dict[str, Any],
    *,
    job_id: str,
    profile: str,
    job_hash: str,
    source_file_id: str,
) -> str:
    status = payload.get("status")
    if status not in {"COMPLETED", "REVIEW"}:
        raise ReceiptError("unexpected done status")
    if payload.get("job_id") != job_id:
        raise ReceiptError("job identity mismatch")
    if payload.get("profile") != profile:
        raise ReceiptError("profile identity mismatch")
    if payload.get("job_hash") != job_hash:
        raise ReceiptError("job hash mismatch")
    source = payload.get("source")
    if not isinstance(source, dict):
        raise ReceiptError("source identity missing")
    if source.get("kind") != "google_drive" or source.get("file_id") != source_file_id:
        raise ReceiptError("source identity mismatch")
    return status


def main(argv: list[str]) -> int:
    if len(argv) == 7 and argv[1] == "inspect-done":
        payload = safe_load(argv[2])
        print(
            _verify_identity(
                payload,
                job_id=argv[3],
                profile=argv[4],
                job_hash=argv[5],
                source_file_id=argv[6],
            )
        )
        return 0
    if len(argv) == 4 and argv[1] == "inspect-failed":
        payload = safe_load(argv[2])
        if payload.get("status") != "FAILED" or payload.get("job_file") != argv[3]:
            raise ReceiptError("failed receipt identity mismatch")
        error_type = str(payload.get("error_type") or "")
        error_code = str(payload.get("error_code") or "")
        if not ERROR_TYPE_RE.fullmatch(error_type) or not ERROR_CODE_RE.fullmatch(error_code):
            raise ReceiptError("failed receipt error identity mismatch")
        print("UV_ERROR_TYPE=" + error_type)
        print("UV_ERROR_CODE=" + error_code)
        return 0
    raise ReceiptError("unsupported receipt operation")


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except (OSError, ReceiptError, TypeError, ValueError):
        print("ERROR: receipt validation failed", file=sys.stderr)
        raise SystemExit(1)
