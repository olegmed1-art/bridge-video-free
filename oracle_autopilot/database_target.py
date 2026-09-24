"""Explicit PostgreSQL target for autopilot only; Neon remains the default."""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, unquote, urlsplit


def backend() -> str:
    value = os.environ.get("AUTOPILOT_DB_BACKEND", "neon")
    if value not in {"neon", "postgresql"}:
        raise ValueError("AUTOPILOT_DB_BACKEND_INVALID")
    return value


def expected_database() -> str:
    if backend() == "neon":
        return "neondb"
    value = os.environ.get("AUTOPILOT_PG_DATABASE", "")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value):
        raise ValueError("AUTOPILOT_PG_DATABASE_INVALID")
    return value


def validate_pinned_dsn(raw: str, *, expected_user: str) -> str:
    """Reject alternate libpq routing options; never echo supplied credentials."""
    if backend() != "postgresql":
        raise ValueError("AUTOPILOT_POSTGRES_BACKEND_REQUIRED")
    host = os.environ.get("AUTOPILOT_PG_HOST", "")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
        raise ValueError("AUTOPILOT_PG_HOST_INVALID")
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", expected_user):
        raise ValueError("AUTOPILOT_PG_PRINCIPAL_REQUIRED")
    try:
        port = int(os.environ.get("AUTOPILOT_PG_PORT", "5432"))
        value = raw.strip()
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        valid = (
            1 <= port <= 65535
            and parsed.scheme in {"postgres", "postgresql"}
            and unquote(parsed.username or "") == expected_user
            and bool(parsed.password)
            and parsed.hostname == host
            and (parsed.port if parsed.port is not None else 5432) == port
            and parsed.path == "/" + expected_database()
            and not parsed.fragment
            and set(query) <= {"sslmode", "channel_binding", "sslrootcert", "connect_timeout", "application_name"}
            and all(len(values) == 1 for values in query.values())
            and query.get("sslmode") == ["verify-full"]
            and query.get("channel_binding") == ["require"]
        )
        if not valid:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("AUTOPILOT_POSTGRES_DSN_INVALID") from None
    return value
