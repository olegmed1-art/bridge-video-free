"""Observe the production app connection; never admit a branch or publish data."""
from __future__ import annotations

import os
import re

from psycopg.rows import tuple_row

from .db import EXPECTED_DATABASE, EXPECTED_PRINCIPAL, PRODUCTION_HOST, connect
from .incident_db_probe import probe

PROJECT = "misty-poetry-18012774"
ENDPOINT = "ep-noisy-pine-b1pe30sf"
# Current upstream uses postmaster for all three tags. Retain the endpoint's
# privileged superuser context supported by the existing project attestor.
TAG_CONTEXTS = {"neon.project_id": {"postmaster"}, "neon.branch_id": {"postmaster"},
                "neon.endpoint_id": {"postmaster", "superuser"}}


class RuntimeIdentityUnavailable(ValueError):
    pass


def _read_identity_rows(conn):
    """Two SELECTs with server statement timeouts and explicit rollback."""
    if conn.autocommit or conn.info.transaction_status != 0:
        raise RuntimeIdentityUnavailable("idle transactional connection required")
    try:
        with conn.cursor(row_factory=tuple_row) as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute("SET LOCAL statement_timeout = '5s'")
            cur.execute("SET LOCAL lock_timeout = '2s'")
            cur.execute("SELECT current_user,pg_catalog.current_database(),"
                        "pg_catalog.current_setting('transaction_read_only'),pg_catalog.statement_timestamp()")
            session = cur.fetchone()
            cur.execute("""SELECT name,setting,context,source,reset_val,pending_restart
                FROM pg_catalog.pg_settings
                WHERE name IN ('neon.project_id','neon.branch_id','neon.endpoint_id')""")
            tags = cur.fetchall()
        return session, tags
    finally:
        conn.rollback()


def observe_runtime_identity() -> dict:
    """Use resident configuration and the same connector as WORLD retrieval.

    No caller-selected target or SQL. Branch is observed, not pinned to a remembered
    default. The existing probe rejects DSN overrides and unsafe libpq environment.
    """
    try:
        if probe(False).get("status") != "configuration_pass_connection_not_tested":
            raise RuntimeIdentityUnavailable("configuration unavailable")
        with connect() as conn:
            if (conn.info.host != PRODUCTION_HOST or conn.info.port != 5432
                    or not conn.pgconn.ssl_in_use):
                raise RuntimeIdentityUnavailable("transport mismatch")
            session, rows = _read_identity_rows(conn)
        if not session or session[:3] != (EXPECTED_PRINCIPAL, EXPECTED_DATABASE, "on"):
            raise RuntimeIdentityUnavailable("session mismatch")
        if len(rows) != 3 or {row[0] for row in rows} != set(TAG_CONTEXTS):
            raise RuntimeIdentityUnavailable("identity tags missing or duplicated")
        tags = {}
        for name, setting, context, source, reset, pending in rows:
            if (context not in TAG_CONTEXTS[name] or source != "configuration file"
                    or reset != setting or pending is not False):
                raise RuntimeIdentityUnavailable("identity provenance mismatch")
            tags[name] = setting
        if (tags["neon.project_id"] != PROJECT or tags["neon.endpoint_id"] != ENDPOINT
                or not re.fullmatch(r"br-[a-z0-9-]{3,100}", tags["neon.branch_id"])):
            raise RuntimeIdentityUnavailable("identity scope mismatch")
        revision = os.environ.get("VERCEL_GIT_COMMIT_SHA", "")
        return {
            "contract_version": "book-runtime-identity-v1",
            "status": "OBSERVED_NOT_ADMITTED",
            "observed_at": session[3].isoformat(),
            "deployment_revision": revision if re.fullmatch(r"[0-9a-f]{40}", revision) else None,
            "connection": {"host": PRODUCTION_HOST, "port": 5432,
                           "database": EXPECTED_DATABASE, "principal": EXPECTED_PRINCIPAL,
                           "tls_in_use": True, "read_only": True},
            "neon": {"project_id": tags["neon.project_id"], "branch_id": tags["neon.branch_id"],
                     "endpoint_id": tags["neon.endpoint_id"], "source": "configuration file",
                     "reset_matches": True, "pending_restart": False},
        }
    except Exception:
        # Do not serialize DB errors, environment values, DSNs, or arbitrary tags.
        raise RuntimeIdentityUnavailable("runtime identity unavailable") from None
