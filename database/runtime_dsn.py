#!/usr/bin/env python3
"""Shared fail-closed validation for Neon runtime connection strings."""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

EXPECTED_DATABASE = "neondb"
NEON_HOST_SUFFIX = ".neon.tech"


def _strip_outer_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def validate_runtime_dsn(
    raw: str,
    *,
    expected_principal: str,
    env_name: str,
    require_pooler: bool = False,
) -> str:
    """Validate a complete Neon PostgreSQL URI without exposing its secret.

    Runtime secrets must be complete connection strings. Password-only values,
    copied .env blocks, generic PostgreSQL endpoints and silent TLS downgrades
    are rejected. The caller can additionally require the Neon pooled endpoint.
    """
    value = _strip_outer_quotes(raw)
    if not value:
        return ""
    if "\n" in value or "\r" in value or "=" in value.split("://", 1)[0]:
        raise RuntimeError(f"{env_name} must be one complete PostgreSQL URI")

    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise RuntimeError(f"{env_name} is not a valid PostgreSQL URI") from exc

    if parsed.scheme not in {"postgresql", "postgres"}:
        raise RuntimeError(f"{env_name} must use the PostgreSQL URI scheme")
    if parsed.fragment:
        raise RuntimeError(f"{env_name} must not contain a URI fragment")

    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    hostname = (parsed.hostname or "").lower().rstrip(".")
    database = unquote(parsed.path.lstrip("/"))

    if username != expected_principal:
        raise RuntimeError(f"{env_name} must authenticate as the dedicated runtime principal")
    if not password:
        raise RuntimeError(f"{env_name} must contain a password")
    if not hostname.endswith(NEON_HOST_SUFFIX):
        raise RuntimeError(f"{env_name} must use a Neon endpoint")
    if require_pooler and "-pooler." not in hostname:
        raise RuntimeError(f"{env_name} must use the Neon pooled endpoint")
    if database != EXPECTED_DATABASE:
        raise RuntimeError(f"{env_name} must target the expected database")
    if parsed.port not in (None, 5432):
        raise RuntimeError(f"{env_name} must use the PostgreSQL port")

    query = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = (query.get("sslmode") or [""])[-1].lower()
    channel_binding = (query.get("channel_binding") or [""])[-1].lower()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise RuntimeError(f"{env_name} must require TLS")
    if channel_binding != "require":
        raise RuntimeError(f"{env_name} must require channel binding")

    return value
