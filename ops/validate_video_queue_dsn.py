#!/usr/bin/env python3
"""Validate one bounded PostgreSQL queue credential without disclosing it."""
from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict


class QueueDsnError(ValueError):
    pass


def validate_dsn_text(raw: str) -> None:
    if not raw or len(raw.encode("utf-8")) > 4096:
        raise QueueDsnError("queue DSN is empty or oversized")
    if raw != raw.strip() or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise QueueDsnError("queue DSN contains whitespace or line breaks")
    try:
        parsed = urlsplit(raw, allow_fragments=True)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise QueueDsnError("queue DSN is structurally invalid") from exc
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.netloc
        or not hostname
        or not parsed.username
        or not parsed.password
        or "#" in raw
        or parsed.fragment
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or port is not None and not 1 <= port <= 65535
    ):
        raise QueueDsnError("queue DSN is not one complete PostgreSQL URI")
    try:
        values = conninfo_to_dict(raw)
    except (psycopg.Error, ValueError) as exc:
        raise QueueDsnError("queue DSN is rejected by PostgreSQL parsing") from exc
    if not all(values.get(name) for name in ("user", "password", "host", "dbname")):
        raise QueueDsnError("queue DSN lacks a required PostgreSQL connection field")


def validate_dsn_file(path: Path) -> None:
    try:
        raw_bytes = path.read_bytes()
        raw = raw_bytes.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise QueueDsnError("queue DSN file is unreadable") from exc
    if len(raw_bytes) > 4096:
        raise QueueDsnError("queue DSN file is oversized")
    validate_dsn_text(raw)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    try:
        validate_dsn_file(args.path)
    except QueueDsnError:
        return 1
    print("VIDEO_QUEUE_DSN_PREFLIGHT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
