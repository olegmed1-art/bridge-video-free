from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

EXPECTED_PRINCIPAL = "bridge_school_app_principal"
EXPECTED_DATABASE = "neondb"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def normalize_dsn(raw: str) -> str:
    value = _unquote(raw)
    if not value:
        raise RuntimeError("BRIDGE_APP_DATABASE_URL is not configured")
    if not value.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("BRIDGE_APP_DATABASE_URL must be a complete PostgreSQL connection URI")

    parsed = urlsplit(value)
    if parsed.username != EXPECTED_PRINCIPAL:
        raise RuntimeError("BRIDGE_APP_DATABASE_URL uses an unexpected database principal")
    if not parsed.password:
        raise RuntimeError("BRIDGE_APP_DATABASE_URL does not include a database password")
    if not parsed.hostname or "-pooler." not in parsed.hostname:
        raise RuntimeError("BRIDGE_APP_DATABASE_URL must use the Neon pooled endpoint")
    if parsed.path != f"/{EXPECTED_DATABASE}":
        raise RuntimeError("BRIDGE_APP_DATABASE_URL targets an unexpected database")
    return value


def database_dsn() -> str:
    return normalize_dsn(os.environ.get("BRIDGE_APP_DATABASE_URL", ""))


@contextmanager
def connect():
    conn = psycopg.connect(
        database_dsn(),
        connect_timeout=10,
        application_name="bridge-school-api",
        row_factory=dict_row,
    )
    try:
        yield conn
    finally:
        conn.close()
