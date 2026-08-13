from __future__ import annotations

import os
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

EXPECTED_PRINCIPAL = "bridge_school_app_principal"
EXPECTED_DATABASE = "neondb"
CONNECT_ATTEMPTS = 3
INITIAL_RETRY_DELAY_SECONDS = 0.25


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


def _open_connection():
    dsn = database_dsn()
    delay = INITIAL_RETRY_DELAY_SECONDS
    for attempt in range(1, CONNECT_ATTEMPTS + 1):
        try:
            return psycopg.connect(
                dsn,
                connect_timeout=10,
                application_name="bridge-school-api",
                row_factory=dict_row,
            )
        except psycopg.OperationalError:
            if attempt >= CONNECT_ATTEMPTS:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("database connection retry loop exited unexpectedly")


@contextmanager
def connect():
    conn = _open_connection()
    try:
        yield conn
    finally:
        conn.close()
