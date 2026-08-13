#!/usr/bin/env python3
"""Fail-closed runtime credential and privilege smoke test for the GitHub worker."""
from __future__ import annotations

import os
import re
import sys

import psycopg

EXPECTED_PRINCIPAL = "bridge_school_worker_principal"
EXPECTED_CAPABILITY = "bridge_school_worker"
EXPECTED_SCHOOL = "Школа спортивного бриджа"


def fail(message: str) -> None:
    print(f"RUNTIME_DB_PREFLIGHT: FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def safe_db_error(exc: BaseException) -> str:
    """Return useful connection diagnostics without echoing credentials."""
    message = str(exc).strip().replace("\n", " | ")
    message = re.sub(r"postgres(?:ql)?://[^\s]+", "postgresql://[redacted]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)(password\s*=\s*)[^\s]+", r"\1[redacted]", message)
    message = re.sub(r"(?i)(passfile\s*=\s*)[^\s]+", r"\1[redacted]", message)
    return message[:1200] or exc.__class__.__name__


def normalize_dsn(raw: str) -> str:
    """Accept a bare URI or a copied .env assignment without exposing it."""
    value = raw.strip()
    if not value:
        return ""

    for prefix in ("BRIDGE_WORKER_DATABASE_URL=", "DATABASE_URL="):
        if value.startswith(prefix):
            value = value[len(prefix):].strip()
            break

    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()

    if not (value.startswith("postgresql://") or value.startswith("postgres://")):
        fail("BRIDGE_WORKER_DATABASE_URL must contain a PostgreSQL URI, not a variable assignment or password-only value")

    return value


def main() -> None:
    dsn = normalize_dsn(os.environ.get("BRIDGE_WORKER_DATABASE_URL", ""))
    if not dsn:
        fail("BRIDGE_WORKER_DATABASE_URL is not configured")

    try:
        with psycopg.connect(
            dsn,
            connect_timeout=10,
            application_name="bridge-video-worker-preflight",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        current_user,
                        pg_has_role(current_user, %s, 'member'),
                        has_table_privilege(current_user, 'public.school', 'SELECT'),
                        has_table_privilege(current_user, 'public.operational_health_policy', 'UPDATE'),
                        has_table_privilege(current_user, 'public.person', 'DELETE')
                    """,
                    (EXPECTED_CAPABILITY,),
                )
                current_user, is_worker, can_read_school, can_mutate_health_policy, can_delete_person = cur.fetchone()

                if current_user != EXPECTED_PRINCIPAL:
                    fail(f"unexpected principal: {current_user}")
                if not is_worker:
                    fail(f"principal does not inherit {EXPECTED_CAPABILITY}")
                if not can_read_school:
                    fail("worker cannot read the school registry")
                if can_mutate_health_policy:
                    fail("worker can mutate operational health policy")
                if can_delete_person:
                    fail("worker has forbidden DELETE capability on person")

                cur.execute("SELECT count(*) FROM public.school WHERE stable_name = %s", (EXPECTED_SCHOOL,))
                if cur.fetchone()[0] != 1:
                    fail("expected school seed is missing or duplicated")

        print(
            "RUNTIME_DB_PREFLIGHT: PASS "
            f"principal={EXPECTED_PRINCIPAL} capability={EXPECTED_CAPABILITY} school=verified"
        )
    except psycopg.Error as exc:
        fail(f"database connection/query failed: {exc.__class__.__name__}: {safe_db_error(exc)}")


if __name__ == "__main__":
    main()
