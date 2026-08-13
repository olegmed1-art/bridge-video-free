#!/usr/bin/env python3
"""Fail-closed runtime credential and operational-health smoke test."""
from __future__ import annotations

import os
import re
import sys
from urllib.parse import quote

import psycopg

EXPECTED_PRINCIPAL = "bridge_school_health_principal"
EXPECTED_CAPABILITY = "bridge_school_health"
NEON_HOST = "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech"
NEON_DATABASE = "neondb"


def fail(message: str) -> None:
    print(f"RUNTIME_DB_HEALTH: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_db_error(exc: BaseException) -> str:
    message = str(exc).strip().replace("\n", " | ")
    message = re.sub(r"postgres(?:ql)?://[^\s]+", "postgresql://[redacted]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(password\s*=\s*)[^\s]+", r"\1[redacted]", message)
    return message[:1200] or exc.__class__.__name__


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1].strip()
    return value


def _password_dsn(password: str) -> str:
    return (
        f"postgresql://{EXPECTED_PRINCIPAL}:{quote(password, safe='')}@{NEON_HOST}/{NEON_DATABASE}"
        "?sslmode=require&channel_binding=require"
    )


def normalize_health_dsn(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    direct = _unquote(value)
    if direct.startswith("postgresql://") or direct.startswith("postgres://"):
        return direct

    env_values: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, item = line.split("=", 1)
        env_values[key.strip()] = _unquote(item)

    for key in ("BRIDGE_HEALTH_DATABASE_URL", "DATABASE_URL", "POSTGRES_URL", "NEON_DATABASE_URL"):
        candidate = env_values.get(key, "").strip()
        if candidate.startswith("postgresql://") or candidate.startswith("postgres://"):
            return candidate
    for key in ("PGPASSWORD", "NEON_PASSWORD", "PASSWORD"):
        password = env_values.get(key, "").strip()
        if password:
            return _password_dsn(password)
    if value and not any(ch.isspace() for ch in value) and "=" not in value:
        return _password_dsn(value)
    fail("BRIDGE_HEALTH_DATABASE_URL does not contain a usable PostgreSQL URI or Neon password")


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
                        has_table_privilege(current_user, 'public.operational_health_policy', 'UPDATE')
                    """,
                    (EXPECTED_CAPABILITY,),
                )
                row = cur.fetchone()
                principal, is_health, can_summary, can_issue, can_person, can_source, can_policy_update = row
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
