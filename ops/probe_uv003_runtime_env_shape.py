#!/usr/bin/env python3
"""Read-only UV003 discriminator for the resident runtime env file shape.

The command accepts no arguments and emits exactly one allowlisted code.  It
never prints a file path, size, line, key value, ownership detail, exception, or
other environment content.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

ENV_FILE = Path("/opt/bridge-school/universal-video/universal-video.env")
SOURCE_KEY = "UNIVERSAL_VIDEO_SOURCE_COMMIT="
MAX_FILE_BYTES = 1_048_576
MAX_LINE_CHARS = 16_384

ALLOWED_CODES = frozenset(
    {
        "MISSING",
        "SYMLINK",
        "NOT_REGULAR",
        "STAT_FAILED",
        "TOO_LARGE",
        "READ_FAILED",
        "EMPTY",
        "NUL_PRESENT",
        "UTF8_INVALID",
        "UTF8_BOM_PRESENT",
        "LINE_TOO_LONG",
        "MALFORMED_NONCOMMENT_LINE",
        "SOURCE_KEY_ZERO",
        "SOURCE_KEY_ONE",
        "SOURCE_KEY_MULTIPLE",
        "INTERNAL_FAILURE",
    }
)


def classify_bytes(raw: bytes) -> str:
    if not raw:
        return "EMPTY"
    if b"\x00" in raw:
        return "NUL_PRESENT"
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "UTF8_INVALID"
    if text.startswith("\ufeff"):
        return "UTF8_BOM_PRESENT"

    lines = text.splitlines()
    if any(len(line) > MAX_LINE_CHARS for line in lines):
        return "LINE_TOO_LONG"

    count = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in line:
            return "MALFORMED_NONCOMMENT_LINE"
        if line.startswith(SOURCE_KEY):
            count += 1

    if count == 0:
        return "SOURCE_KEY_ZERO"
    if count == 1:
        return "SOURCE_KEY_ONE"
    return "SOURCE_KEY_MULTIPLE"


def probe(path: Path = ENV_FILE) -> str:
    try:
        if path.is_symlink():
            return "SYMLINK"
        if not path.exists():
            return "MISSING"
        info = path.stat()
    except OSError:
        return "STAT_FAILED"
    if not stat.S_ISREG(info.st_mode):
        return "NOT_REGULAR"
    if info.st_size > MAX_FILE_BYTES:
        return "TOO_LARGE"
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            raw = os.read(fd, MAX_FILE_BYTES + 1)
        finally:
            os.close(fd)
    except OSError:
        return "READ_FAILED"
    if len(raw) > MAX_FILE_BYTES:
        return "TOO_LARGE"
    return classify_bytes(raw)


def main() -> int:
    if len(sys.argv) != 1:
        return 2
    try:
        code = probe()
    except Exception:  # Preserve a fixed non-sensitive external surface.
        code = "INTERNAL_FAILURE"
    if code not in ALLOWED_CODES:
        code = "INTERNAL_FAILURE"
    print(f"UV003_RUNTIME_ENV_SHAPE_CODE={code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
