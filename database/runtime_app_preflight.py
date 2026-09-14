#!/usr/bin/env python3
"""Fail-closed runtime credential and privilege smoke test for the interactive app."""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qs, urlsplit, urlunsplit

import psycopg

EXPECTED_PRINCIPAL = "bridge_school_app_principal"
EXPECTED_CAPABILITY = "bridge_school_app"
EXPECTED_SCHOOL = "Школа спортивного бриджа"
EXPECTED_HOST = "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech"
EXPECTED_DATABASE = "neondb"


def fail(message: str) -> None:
    print(f"RUNTIME_DB_APP: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_db_error(exc: BaseException) -> str:
    message = str(exc).strip().replace("\n", " | ")
    message = re.sub(r"postgres(?:ql)?://[^\s]+", "postgresql://[redacted]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(password\s*=\s*)[^\s]+", r"\1[redacted]", message)
    message = re.sub(r"(?i)(passfile\s*=\s*)[^\s]+", r"\1[redacted]", message)
    return message[:1200] or exc.__class__.__name__


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _canonicalize_neon_endpoint(value: str, parsed):
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith(".neon.tech") or hostname == EXPECTED_HOST:
        return value, parsed

    userinfo, separator, hostport = parsed.netloc.rpartition("@")
    if not separator:
        return value, parsed
    _, port_separator, port = hostport.partition(":")
    canonical_hostport = EXPECTED_HOST + (f":{port}" if port_separator else "")
    canonical = urlunsplit(parsed._replace(netloc=f"{userinfo}@{canonical_hostport}"))
    return canonical, urlsplit(canonical)


def normalize_dsn(raw: str) -> str:
    """Require the full pooled production Neon URI for the app principal."""
    value = _unquote(raw)
    if not value:
        return ""
    if not value.startswith(("postgresql://", "postgres://")):
        fail("BRIDGE_APP_DATABASE_URL must be a complete PostgreSQL URI")

    try:
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        fail("BRIDGE_APP_DATABASE_URL is not a valid PostgreSQL URI")

    value, parsed = _canonicalize_neon_endpoint(value, parsed)

    if parsed.username != EXPECTED_PRINCIPAL:
        fail("BRIDGE_APP_DATABASE_URL uses the wrong database principal")
    if not parsed.password:
        fail("BRIDGE_APP_DATABASE_URL is missing a password")
    if parsed.hostname != EXPECTED_HOST:
        fail("BRIDGE_APP_DATABASE_URL must target the production Neon pooled endpoint")
    if parsed.port not in (None, 5432):
        fail("BRIDGE_APP_DATABASE_URL uses an unexpected port")
    if parsed.path != f"/{EXPECTED_DATABASE}":
        fail("BRIDGE_APP_DATABASE_URL uses the wrong database")
    if parsed.fragment:
        fail("BRIDGE_APP_DATABASE_URL must not include a URI fragment")
    if query.get("sslmode") not in (["require"], ["verify-full"]):
        fail("BRIDGE_APP_DATABASE_URL must require TLS")
    if query.get("channel_binding") != ["require"]:
        fail("BRIDGE_APP_DATABASE_URL must require channel binding")

    return value


def main() -> None:
    dsn = normalize_dsn(os.environ.get("BRIDGE_APP_DATABASE_URL", ""))
    if not dsn:
        fail("BRIDGE_APP_DATABASE_URL is not configured")

    try:
        with psycopg.connect(
            dsn,
            connect_timeout=10,
            application_name="bridge-school-app-preflight",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_user,
                        pg_has_role(current_user, %s, 'member'),
                        has_table_privilege(current_user, 'public.school', 'SELECT'),
                        has_table_privilege(current_user, 'public.person', 'INSERT'),
                        has_table_privilege(current_user, 'public.person', 'UPDATE'),
                        has_table_privilege(current_user, 'public.person', 'DELETE'),
                        has_table_privilege(current_user, 'public.source_observation', 'INSERT'),
                        has_table_privilege(current_user, 'public.analysis_run', 'INSERT'),
                        has_table_privilege(current_user, 'public.operational_health_policy', 'UPDATE'),
                        has_schema_privilege(current_user, 'public', 'CREATE')
                    """,
                    (EXPECTED_CAPABILITY,),
                )
                (
                    principal,
                    is_app,
                    can_read_school,
                    can_insert_person,
                    can_update_person,
                    can_delete_person,
                    can_insert_source_observation,
                    can_insert_analysis_run,
                    can_update_health_policy,
                    can_create_schema_objects,
                ) = cur.fetchone()

                if principal != EXPECTED_PRINCIPAL:
                    fail(f"unexpected principal: {principal}")
                if not is_app:
                    fail(f"principal does not inherit {EXPECTED_CAPABILITY}")
                if not can_read_school:
                    fail("app cannot read the school registry")
                if can_insert_person:
                    fail("app can bypass controlled onboarding by inserting person")
                if not can_update_person:
                    fail("app lacks expected existing-person update capability")
                if can_delete_person:
                    fail("app has forbidden DELETE capability on person")
                if can_insert_source_observation:
                    fail("app has forbidden source-observation ingestion capability")
                if can_insert_analysis_run:
                    fail("app has forbidden worker analysis capability")
                if can_update_health_policy:
                    fail("app can mutate operational health policy")
                if can_create_schema_objects:
                    fail("app can create schema objects")

                cur.execute("SELECT count(*) FROM public.school WHERE stable_name = %s", (EXPECTED_SCHOOL,))
                if cur.fetchone()[0] != 1:
                    fail("expected school seed is missing or duplicated")

        print(
            "RUNTIME_DB_APP: PASS "
            f"principal={EXPECTED_PRINCIPAL} capability={EXPECTED_CAPABILITY} "
            "controlled_onboarding=verified school=verified"
        )
    except psycopg.Error as exc:
        fail(f"database connection/query failed: {exc.__class__.__name__}: {safe_db_error(exc)}")


if __name__ == "__main__":
    main()
