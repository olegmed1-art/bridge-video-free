#!/usr/bin/env python3
"""Fail-closed runtime credential and operational-health smoke test."""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qs, urlsplit

import psycopg

EXPECTED_PRINCIPAL = "bridge_school_health_principal"
EXPECTED_CAPABILITY = "bridge_school_health"
EXPECTED_DATABASE = "neondb"
EXPECTED_HOSTS = {
    "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech",
    "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech",
}


def fail(message: str) -> None:
    print(f"RUNTIME_DB_HEALTH: FAIL: {message}", file=sys.stderr)
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


def normalize_health_dsn(raw: str) -> str:
    """Require a full production Neon URI for the dedicated health principal."""
    value = _unquote(raw)
    if not value:
        return ""
    if not value.startswith(("postgresql://", "postgres://")):
        fail("BRIDGE_HEALTH_DATABASE_URL must be a complete PostgreSQL URI")

    try:
        parsed = urlsplit(value)
        query = parse_qs(parsed.query, keep_blank_values=True)
    except ValueError:
        fail("BRIDGE_HEALTH_DATABASE_URL is not a valid PostgreSQL URI")

    if parsed.username != EXPECTED_PRINCIPAL:
        fail("BRIDGE_HEALTH_DATABASE_URL uses the wrong database principal")
    if not parsed.password:
        fail("BRIDGE_HEALTH_DATABASE_URL is missing a password")
    if parsed.hostname not in EXPECTED_HOSTS:
        fail("BRIDGE_HEALTH_DATABASE_URL must target the production Neon endpoint")
    if parsed.port not in (None, 5432):
        fail("BRIDGE_HEALTH_DATABASE_URL uses an unexpected port")
    if parsed.path != f"/{EXPECTED_DATABASE}":
        fail("BRIDGE_HEALTH_DATABASE_URL uses the wrong database")
    if parsed.fragment:
        fail("BRIDGE_HEALTH_DATABASE_URL must not include a URI fragment")
    if query.get("sslmode") not in (["require"], ["verify-full"]):
        fail("BRIDGE_HEALTH_DATABASE_URL must require TLS")
    if query.get("channel_binding") != ["require"]:
        fail("BRIDGE_HEALTH_DATABASE_URL must require channel binding")

    return value


def main() -> None:
    dsn = normalize_health_dsn(os.environ.get("BRIDGE_HEALTH_DATABASE_URL", ""))
    if not dsn:
        fail("BRIDGE_HEALTH_DATABASE_URL is not configured")
    try:
        with psycopg.connect(dsn, connect_timeout=10, application_name="bridge-school-health-monitor") as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_user,
                        pg_has_role(current_user, %s, 'member'),
                        has_table_privilege(current_user, 'public.operational_health_summary', 'SELECT'),
                        has_table_privilege(current_user, 'public.operational_health_issue', 'SELECT'),
                        has_table_privilege(current_user, 'public.person', 'SELECT'),
                        has_table_privilege(current_user, 'public.source', 'SELECT'),
                        has_table_privilege(current_user, 'public.operational_health_policy', 'UPDATE'),
                        has_schema_privilege(current_user, 'public', 'CREATE')
                    """,
                    (EXPECTED_CAPABILITY,),
                )
                row = cur.fetchone()
                (
                    principal,
                    is_health,
                    can_summary,
                    can_issue,
                    can_person,
                    can_source,
                    can_policy_update,
                    can_create_schema_objects,
                ) = row
                if principal != EXPECTED_PRINCIPAL:
                    fail(f"unexpected principal: {principal}")
                if not is_health:
                    fail(f"principal does not inherit {EXPECTED_CAPABILITY}")
                if not (can_summary and can_issue):
                    fail("health principal cannot read operational health views")
                if can_person or can_source:
                    fail("health principal has forbidden school/source data access")
                if can_policy_update:
                    fail("health principal can mutate operational health policy")
                if can_create_schema_objects:
                    fail("health principal can create schema objects")

                cur.execute(
                    "SELECT overall_severity, critical_signal_count, warning_signal_count, ok_signal_count FROM public.operational_health_summary"
                )
                summary = cur.fetchone()
                if not summary:
                    fail("operational_health_summary returned no row")
                severity, critical_count, warning_count, ok_count = summary
                print(
                    "RUNTIME_DB_HEALTH: "
                    f"severity={severity} critical={critical_count} warning={warning_count} ok={ok_count}"
                )
                if severity == "critical" or int(critical_count or 0) > 0:
                    fail("critical operational health signal detected")

        print(
            "RUNTIME_DB_HEALTH: PASS "
            f"principal={EXPECTED_PRINCIPAL} capability={EXPECTED_CAPABILITY}"
        )
    except psycopg.Error as exc:
        fail(f"database connection/query failed: {exc.__class__.__name__}: {safe_db_error(exc)}")


if __name__ == "__main__":
    main()
