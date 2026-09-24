"""Secret-safe diagnostic for #1911; no connection unless --connect is supplied.

Run as a module in the affected deployment's protected environment. This does
not provision credentials, expose an HTTP route, or authorize a deployment.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

from bridge_school_api.db import (
    EXPECTED_DATABASE,
    EXPECTED_PRINCIPAL,
    PRODUCTION_DIRECT_HOST,
    PRODUCTION_HOST,
    _unquote,
    database_dsn,
)


def probe(connect: bool = False) -> dict[str, object]:
    result: dict[str, object] = {"probe": "incident-1911-v2", "connected": False}
    if os.environ.get("VERCEL_ENV") != "production":
        return {**result, "status": "production_environment_required"}
    if any(key.startswith("PG") for key in os.environ):
        return {**result, "status": "libpq_environment_requires_review"}
    try:
        dsn = database_dsn()
        params = conninfo_to_dict(dsn)
        raw_host = (urlsplit(_unquote(os.environ.get("BRIDGE_APP_DATABASE_URL", ""))).hostname or "").lower()
        normalized_host = (urlsplit(dsn).hostname or "").lower()
    except Exception:
        return {**result, "status": "configuration_failed"}

    # The application may rewrite ANY Neon URI authority to its pinned host.
    # Preserve that distinction as booleans; never serialize the original URI
    # or host. These observations are not password or branch-integrity proof.
    source = {
        "raw_host_is_expected": raw_host in {PRODUCTION_HOST, PRODUCTION_DIRECT_HOST},
        "raw_host_is_pooler": raw_host == PRODUCTION_HOST,
        "raw_host_is_direct": raw_host == PRODUCTION_DIRECT_HOST,
        "endpoint_was_rewritten": raw_host != normalized_host,
    }
    result["source"] = source

    # libpq URI query parameters can override URI authority fields. Validate
    # the effective parameters, not just the normalizer's hostname check.
    allowed = {"user", "password", "dbname", "host", "port", "sslmode", "channel_binding"}
    checks = {
        "principal_matches": params.get("user") == EXPECTED_PRINCIPAL,
        "database_matches": params.get("dbname") == EXPECTED_DATABASE,
        "endpoint_matches": params.get("host") == PRODUCTION_HOST,
        "port_matches": params.get("port", "5432") == "5432",
        "tls_required": params.get("sslmode") in {"require", "verify-full"},
        "channel_binding_required": params.get("channel_binding") == "require",
        "password_present": bool(params.get("password")),
        "parameters_allowed": not (params.keys() - allowed),
    }
    result["checks"] = checks
    if not all(checks.values()):
        return {**result, "status": "effective_parameters_rejected"}
    if not source["raw_host_is_expected"]:
        return {**result, "status": "source_endpoint_requires_review"}
    if not connect:
        return {**result, "status": "configuration_pass_connection_not_tested"}
    try:
        with psycopg.connect(
            dsn,
            connect_timeout=10,
            application_name="incident-1911-readonly-probe",
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user, current_database(), current_setting('transaction_read_only')")
                row = cur.fetchone()
            # Even the read-only diagnostic transaction is explicitly rolled back.
            conn.rollback()
        matches = row == (EXPECTED_PRINCIPAL, EXPECTED_DATABASE, "on")
        return {**result, "connected": True, "status": "pass" if matches else "session_contract_failed"}
    except Exception as exc:
        # Never serialize an exception, DSN, password, or arbitrary server text.
        message = str(exc).lower()
        category = "connection_failed"
        if "authentication failed" in message:
            category = "authentication_failed"
        elif "timeout" in message:
            category = "connection_timeout"
        return {**result, "status": category}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="perform one read-only connection probe")
    args = parser.parse_args()
    result = probe(args.connect)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"pass", "configuration_pass_connection_not_tested"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
