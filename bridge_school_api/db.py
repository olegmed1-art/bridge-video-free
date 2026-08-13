from __future__ import annotations

import os
from contextlib import contextmanager
from urllib.parse import quote

import psycopg
from psycopg.rows import dict_row

EXPECTED_PRINCIPAL = "bridge_school_app_principal"
NEON_HOST = "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech"
NEON_DATABASE = "neondb"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def build_password_dsn(password: str) -> str:
    return (
        f"postgresql://{EXPECTED_PRINCIPAL}:{quote(password, safe='')}@{NEON_HOST}/{NEON_DATABASE}"
        "?sslmode=require&channel_binding=require"
    )


def normalize_dsn(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise RuntimeError("BRIDGE_APP_DATABASE_URL is not configured")

    direct = _unquote(value)
    if direct.startswith(("postgresql://", "postgres://")):
        return direct

    env_values: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, item = line.split("=", 1)
        env_values[key.strip()] = _unquote(item)

    for key in ("BRIDGE_APP_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL"):
        candidate = env_values.get(key, "").strip()
        if candidate.startswith(("postgresql://", "postgres://")):
            return candidate

    for key in ("PGPASSWORD", "NEON_PASSWORD", "PASSWORD"):
        password = env_values.get(key, "").strip()
        if password:
            return build_password_dsn(password)

    if value and not any(ch.isspace() for ch in value) and "=" not in value:
        return build_password_dsn(value)

    raise RuntimeError("BRIDGE_APP_DATABASE_URL does not contain a usable PostgreSQL connection value")


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
