from __future__ import annotations

import os
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit, urlunsplit

import psycopg
from psycopg.rows import dict_row

EXPECTED_PRINCIPAL = "bridge_school_app_principal"
EXPECTED_DATABASE = "neondb"
PRODUCTION_HOST = "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech"
PREVIEW_HOST = "ep-wandering-night-b1ej3ow6-pooler.c-5.eu-central-1.aws.neon.tech"
PRODUCTION_DIRECT_HOST = PRODUCTION_HOST.replace("-pooler", "")
PREVIEW_DIRECT_HOST = PREVIEW_HOST.replace("-pooler", "")
CONNECT_ATTEMPTS = 3
INITIAL_RETRY_DELAY_SECONDS = 0.25


class DatabaseConfigurationError(RuntimeError):
    """Raised when the application database credential violates the runtime contract."""


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _allowed_hosts() -> set[str]:
    environment = os.environ.get("VERCEL_ENV", "").strip().lower()
    if environment == "production":
        return {PRODUCTION_HOST}
    if environment == "preview":
        return {PREVIEW_HOST}
    # Local/CI validation may exercise either deployment contract explicitly.
    return {PRODUCTION_HOST, PREVIEW_HOST}


def _canonicalize_neon_endpoint(value: str, parsed):
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith(".neon.tech"):
        return value, parsed

    environment = os.environ.get("VERCEL_ENV", "").strip().lower()
    if environment == "production":
        pooled_host = PRODUCTION_HOST
    elif environment == "preview":
        pooled_host = PREVIEW_HOST
    else:
        pooled_host = {
            PRODUCTION_DIRECT_HOST: PRODUCTION_HOST,
            PREVIEW_DIRECT_HOST: PREVIEW_HOST,
        }.get(hostname)
    if not pooled_host or hostname == pooled_host:
        return value, parsed

    userinfo, separator, hostport = parsed.netloc.rpartition("@")
    if not separator:
        return value, parsed
    _, port_separator, port = hostport.partition(":")
    canonical_hostport = pooled_host + (f":{port}" if port_separator else "")
    canonical = urlunsplit(parsed._replace(netloc=f"{userinfo}@{canonical_hostport}"))
    return canonical, urlsplit(canonical)


def normalize_dsn(raw: str) -> str:
    value = _unquote(raw)
    if not value:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL is not configured")
    if not value.startswith(("postgresql://", "postgres://")):
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL must be a complete PostgreSQL connection URI")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL is not a valid PostgreSQL URI") from exc

    if parsed.username != EXPECTED_PRINCIPAL:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL uses an unexpected database principal")
    if not parsed.password:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL does not include a database password")

    value, parsed = _canonicalize_neon_endpoint(value, parsed)
    hostname = (parsed.hostname or "").lower()
    if hostname not in _allowed_hosts():
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL targets an unexpected Neon endpoint")
    if parsed.port not in (None, 5432):
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL uses an unexpected database port")
    if parsed.path != f"/{EXPECTED_DATABASE}":
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL targets an unexpected database")
    if parsed.fragment:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL must not include a URI fragment")

    query = parse_qs(parsed.query, keep_blank_values=True)
    sslmodes = query.get("sslmode", [])
    if len(sslmodes) != 1 or sslmodes[0] not in {"require", "verify-full"}:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL must require TLS")
    if query.get("channel_binding", []) != ["require"]:
        raise DatabaseConfigurationError("BRIDGE_APP_DATABASE_URL must require channel binding")

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
