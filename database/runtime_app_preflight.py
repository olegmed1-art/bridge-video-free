#!/usr/bin/env python3
"""Fail-closed runtime credential and privilege smoke test for the interactive app."""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import quote

import psycopg

EXPECTED_PRINCIPAL = "bridge_school_app_principal"
EXPECTED_CAPABILITY = "bridge_school_app"
EXPECTED_SCHOOL = "Школа спортивного бриджа"
NEON_HOST = "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech"
NEON_DATABASE = "neondb"


def fail(message: str) -> None:
    print(f"RUNTIME_DB_APP: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_db_error(exc: BaseException) -> str:
    message = str(exc).strip().replace("\n", " | ")
    message = re.sub(r"postgres(?:ql)?://[^\s]+", "postgresql://[redacted]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(password\s*=\s*)[^\s]+", r"\1[redacted]", message)
    message = re.sub(r"(?i)(passfile\s*=\s*)[^\s]+", r"\1[redacted]", message)
    return message[:1200] or exc.__class__.__name__


def unquote_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def build_password_dsn(password: str) -> str:
    encoded_password = quote(password, safe="")
    return (
        f"postgresql://{EXPECTED_PRINCIPAL}:{encoded_password}@{NEON_HOST}/{NEON_DATABASE}"
        "?sslmode=require&channel_binding=require"
    )


def normalize_dsn(raw: str) -> str:
    """Accept a URI, one env assignment, a full Neon .env file, or password only."""
    value = raw.strip()
    if not value:
        return ""

    direct = unquote_env_value(value)
    if direct.startswith("postgresql://") or direct.startswith("postgres://"):
        return direct

    env_values: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, item = line.split("=", 1)
        env_values[key.strip()] = unquote_env_value(item)

    for key in ("BRIDGE_APP_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL"):
        candidate = env_values.get(key, "").strip()
        if candidate.startswith("postgresql://") or candidate.startswith("postgres://"):
            return candidate

    for key in ("PGPASSWORD", "NEON_PASSWORD", "PASSWORD"):
        password = env_values.get(key, "").strip()
        if password:
            return build_password_dsn(password)

    if value and not any(ch.isspace() for ch in value) and "=" not in value:
        return build_password_dsn(value)

    fail("BRIDGE_APP_DATABASE_URL does not contain a usable PostgreSQL URI or Neon password")


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
                if not (can_insert_person and can_update_person):
                    fail("app lacks expected interactive person write capability")
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
            f"principal={EXPECTED_PRINCIPAL} capability={EXPECTED_CAPABILITY} school=verified"
        )
    except psycopg.Error as exc:
        fail(f"database connection/query failed: {exc.__class__.__name__}: {safe_db_error(exc)}")


if __name__ == "__main__":
    main()
