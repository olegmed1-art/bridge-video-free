#!/usr/bin/env python3
"""Fail-closed runtime credential and operational-health smoke test."""
from __future__ import annotations

import os
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
        port = parsed.port
    except ValueError:
        fail("HEALTH_DSN_INVALID_URI")

    if parsed.username != EXPECTED_PRINCIPAL:
        fail("BRIDGE_HEALTH_DATABASE_URL uses the wrong database principal")
    if not parsed.password:
        fail("BRIDGE_HEALTH_DATABASE_URL is missing a password")
    if parsed.hostname not in EXPECTED_HOSTS:
        fail("BRIDGE_HEALTH_DATABASE_URL must target the production Neon endpoint")
    if port not in (None, 5432):
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


# Only these JSON members may leave the health view as diagnostic counters.
AUTOPILOT_COUNTER_QUERY = """
    SELECT signal_key, severity,
        CASE signal_key
            WHEN 'autopilot_mailbox_capacity' THEN jsonb_build_object(
                'used_dispatches', details->'used_dispatches',
                'max_dispatches', details->'max_dispatches',
                'remaining', details->'remaining')
            WHEN 'autopilot_planner_backlog' THEN jsonb_build_object(
                'paused_count', details->'paused_count',
                'open_actionable_count', details->'open_actionable_count')
        END AS counters
    FROM public.autopilot_operational_health_signal ORDER BY signal_key
"""
AUTOPILOT_SIGNAL_KEYS = frozenset({
    "autopilot_mailbox_capacity", "autopilot_planner_backlog", "autopilot_mailbox_e2e",
})
AUTOPILOT_COUNTER_FIELDS = {
    "autopilot_mailbox_capacity": ("used_dispatches", "max_dispatches", "remaining"),
    "autopilot_planner_backlog": ("paused_count", "open_actionable_count"),
}


def autopilot_counter_line(key, details):
    fields = AUTOPILOT_COUNTER_FIELDS.get(key)
    if fields is None:
        return None
    unavailable = f"RUNTIME_AUTOPILOT_COUNTERS_UNAVAILABLE: signal={key}"
    if type(details) is not dict:
        return unavailable
    values = [details.get(field) for field in fields]
    if any(type(value) is not int or abs(value) > 2**31-1 for value in values):
        return unavailable
    if key == "autopilot_mailbox_capacity":
        used, maximum, remaining = values
        # Negative remaining is valid when capacity is exceeded.
        if used < 0 or not 1 <= maximum <= 100 or remaining != maximum-used:
            return unavailable
    elif any(value < 0 for value in values):
        return unavailable
    return f"RUNTIME_AUTOPILOT_COUNTERS: signal={key} " + " ".join(
        f"{field}={value}" for field, value in zip(fields, values)
    )


def check_autopilot_health(cur, *, required=False):
    cur.execute("SELECT to_regclass('public.autopilot_operational_health_signal')")
    if cur.fetchone()[0] is not None:
        cur.execute(AUTOPILOT_COUNTER_QUERY)
        autopilot_signals = cur.fetchall()
        if required and not autopilot_signals:
            fail("Oracle autopilot health view returned no signals")
        autopilot_critical = [key for key, sev, _ in autopilot_signals if sev == "critical"]
        autopilot_warning = [key for key, sev, _ in autopilot_signals if sev == "warning"]
        print(
            "RUNTIME_AUTOPILOT_HEALTH: "
            f"critical={len(autopilot_critical)} warning={len(autopilot_warning)} "
            f"signals={len(autopilot_signals)}"
        )
        for key, _, counters in autopilot_signals:
            line = autopilot_counter_line(key, counters)
            if line is not None:
                print(line)
        if autopilot_critical:
            fail(
                "critical Autopilot operational health signal detected: "
                + ",".join(key if key in AUTOPILOT_SIGNAL_KEYS else "unknown_signal"
                           for key in autopilot_critical)
            )
    else:
        if required:
            fail("Oracle autopilot health view is missing")
        print("RUNTIME_AUTOPILOT_HEALTH: SKIP view_not_installed")


def main() -> None:
    dsn = normalize_health_dsn(os.environ.get("BRIDGE_HEALTH_DATABASE_URL", ""))
    if not dsn:
        fail("BRIDGE_HEALTH_DATABASE_URL is not configured")
    backend = os.environ.get("AUTOPILOT_DB_BACKEND", "neon")
    if backend not in {"neon", "postgresql"}:
        fail("unknown autopilot health backend")
    autopilot_dsn = None
    if backend == "postgresql":
        from oracle_autopilot.database_target import validate_pinned_dsn
        try:
            autopilot_dsn = validate_pinned_dsn(os.environ.get("AUTOPILOT_HEALTH_DATABASE_URL", ""), expected_user=EXPECTED_PRINCIPAL)
        except ValueError:
            fail("Oracle autopilot health connection is not pinned")
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
                    fail("unexpected principal")
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

                if autopilot_dsn is None:
                    check_autopilot_health(cur)

        if autopilot_dsn is not None:
            with psycopg.connect(autopilot_dsn, connect_timeout=10, application_name="autopilot-oracle-health-monitor") as oracle:
                oracle.read_only = True
                with oracle.cursor() as cur:
                    cur.execute("""SELECT current_user, current_database(),
                        pg_has_role(current_user, 'bridge_school_health', 'member'),
                        has_table_privilege(current_user, 'public.autopilot_operational_health_signal', 'SELECT'),
                        has_table_privilege(current_user, 'autopilot.task', 'INSERT,UPDATE,DELETE,TRUNCATE'),
                        has_schema_privilege(current_user, 'public', 'CREATE'),
                        rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
                        FROM pg_roles WHERE rolname=current_user""")
                    row = cur.fetchone()
                    if not row or row[:4] != (EXPECTED_PRINCIPAL, "autopilot", True, True) or any(row[4:]):
                        fail("Oracle autopilot health principal exceeds or lacks required privileges")
                    check_autopilot_health(cur, required=True)

        print(
            "RUNTIME_DB_HEALTH: PASS "
            f"principal={EXPECTED_PRINCIPAL} capability={EXPECTED_CAPABILITY}"
        )
    except psycopg.Error:
        fail("database connection/query failed")


if __name__ == "__main__":
    main()
