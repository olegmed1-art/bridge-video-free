"""Secret-safe diagnostic for #1911; no connection unless --connect is supplied.

Run as a module in the affected deployment's protected environment. This does
not provision credentials, expose an HTTP route, or authorize a deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import time
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

from bridge_school_api.incident_error_detail import (
    MAX_MESSAGE_CHARS, connection_error_detail, has_explicit_timeout,
)


_SAFE_SQLSTATES = frozenset({
    "08000", "08001", "08003", "08004", "08006", "08007", "08P01",
    "28000", "28P01", "42501", "25006", "53300", "53400",
    "57014", "57P01", "57P02", "57P03",
})
_SAFE_EXCEPTION_CLASSES = (
    (psycopg.errors.ConnectionTimeout, "ConnectionTimeout"),
    (psycopg.OperationalError, "OperationalError"),
    (psycopg.InterfaceError, "InterfaceError"),
    (psycopg.DatabaseError, "DatabaseError"),
    (psycopg.Error, "Error"),
    (TimeoutError, "TimeoutError"),
    (RuntimeError, "RuntimeError"),
)
_DIAGNOSTIC_PHRASES = (
    ("dns", ("could not translate host name", "name or service not known",
             "temporary failure in name resolution", "nodename nor servname provided")),
    ("tcp", ("connection refused", "network is unreachable", "no route to host",
             "connection reset by peer")),
    ("tls", ("ssl error:", "ssl syscall error:", "certificate verify failed",
             "ssl handshake failed", "tls handshake failed", "server does not support ssl",
             "ssl connection has been closed unexpectedly")),
    ("channel_binding", ("channel binding required", "channel binding is required",
                         "channel binding mismatch")),
    ("auth", ("authentication failed", "no password supplied", "no pg_hba.conf entry")),
    ("server", ("server closed the connection unexpectedly", "database system is starting up",
                "database system is shutting down", "database system is in recovery mode",
                "terminating connection due to administrator command", "too many connections",
                "remaining connection slots are reserved")),
    ("timeout", ()),  # Added only for an explicit expiration phrase or timeout type.
)


def _diagnostic_markers(message: str, exc: Exception, sqlstate: str | None) -> list[str]:
    # Fixed hints only, not a root-cause verdict. libpq may aggregate multiple
    # attempts: collect every recognized category, never select only the first.
    found = {marker for marker, phrases in _DIAGNOSTIC_PHRASES
             if any(phrase in message for phrase in phrases)}
    if sqlstate in {"28000", "28P01"}:
        found.add("auth")
    if sqlstate in {"53300", "53400", "57P01", "57P02", "57P03"}:
        found.add("server")
    if isinstance(exc, (TimeoutError, psycopg.errors.ConnectionTimeout)) or has_explicit_timeout(message):
        found.add("timeout")
    # No arbitrary message, host, address, SQLSTATE or exception name is copied.
    return [marker for marker, _ in _DIAGNOSTIC_PHRASES if marker in found] or ["unknown"]


def _elapsed_ms(started_ns: int) -> int:
    # Monotonic duration only; bounded by the existing ten-minute CI job budget.
    return min(600_000, max(0, (time.monotonic_ns() - started_ns) // 1_000_000))


def _failure(exc: Exception, phase: str, started_ns: int) -> dict[str, object]:
    # Never use arbitrary exception class names, attributes or server text as
    # output. Unknown SQLSTATEs are deliberately not echoed, even if five chars.
    sqlstate_unreadable = False
    try:
        code = getattr(exc, "sqlstate", None)
    except Exception:
        code = None
        sqlstate_unreadable = True
    sqlstate = code if type(code) is str and code in _SAFE_SQLSTATES else None
    # present means a non-None value was observed. filtered also covers an
    # unreadable attribute: (False, True) must not be interpreted as absent.
    sqlstate_present = code is not None
    sqlstate_filtered = sqlstate_unreadable or (sqlstate_present and sqlstate is None)
    exception_class = next((name for kind, name in _SAFE_EXCEPTION_CLASSES if isinstance(exc, kind)), "Exception")
    try:
        raw_message = str(exc)
        message = raw_message.lower() if len(raw_message) <= MAX_MESSAGE_CHARS else ""
    except Exception:
        raw_message = None
        message = ""
    prefix = "connection" if phase == "connect" else phase
    status = prefix + "_failed"
    if phase == "connect" and (sqlstate in {"28000", "28P01"} or "authentication failed" in message):
        status = "authentication_failed"
    elif isinstance(exc, (TimeoutError, psycopg.errors.ConnectionTimeout)) or has_explicit_timeout(message):
        status = prefix + "_timeout"
    # Categories remain hints. The phase identifies a code operation, never a
    # proven DNS/TCP/TLS/authentication substage of psycopg.connect().
    failure = {"status": status, "phase": phase, "phase_elapsed_ms": _elapsed_ms(started_ns),
            "exception_class": exception_class, "sqlstate": sqlstate,
            "sqlstate_present": sqlstate_present, "sqlstate_filtered": sqlstate_filtered,
            "diagnostic_markers": _diagnostic_markers(message, exc, sqlstate)}
    if phase == "connect":
        failure["error_detail"] = connection_error_detail(
            raw_message, expected_host=PRODUCTION_HOST,
            timeout_exception=isinstance(exc, (TimeoutError, psycopg.errors.ConnectionTimeout)),
        )
    return failure


def probe(connect: bool = False) -> dict[str, object]:
    # connected records successful return from connect(), not an open session at
    # report time. Cleanup is attempted for every established connection.
    result: dict[str, object] = {"probe": "incident-1911-v5", "connected": False}
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
    started_ns = phase_started_ns = time.monotonic_ns()
    phase = "connect"
    conn = None
    failure = None
    cleanup_errors = []
    try:
        conn = psycopg.connect(
            dsn,
            connect_timeout=10,
            application_name="incident-1911-readonly-probe",
        )
        result["connected"] = True
        phase = "transaction_setup"
        phase_started_ns = time.monotonic_ns()
        # With autocommit=False, psycopg starts BEGIN READ ONLY before the
        # first cursor command, without transaction settings in startup options.
        conn.read_only = True
        with conn.cursor() as cur:
            # BEGIN/SET rely on the existing outer watchdog. The 5000 ms limit
            # protects the SELECT after SET LOCAL applies in this transaction.
            cur.execute("SET LOCAL statement_timeout = 5000")
            phase = "query"
            phase_started_ns = time.monotonic_ns()
            cur.execute("SELECT current_user, current_database(), current_setting('transaction_read_only')")
            row = cur.fetchone()
        if row != (EXPECTED_PRINCIPAL, EXPECTED_DATABASE, "on"):
            failure = {"status": "session_contract_failed", "phase": "query",
                       "phase_elapsed_ms": _elapsed_ms(phase_started_ns),
                       "exception_class": None, "sqlstate": None,
                       "sqlstate_present": False, "sqlstate_filtered": False,
                       "diagnostic_markers": ["unknown"]}
    except Exception as exc:
        failure = _failure(exc, phase, phase_started_ns)
    finally:
        if conn is not None:
            # Explicit cleanup has no context-manager success commit. Attempt
            # both operations once, retaining the first failure if cleanup fails.
            for phase in ("rollback", "close"):
                phase_started_ns = time.monotonic_ns()
                try:
                    if phase == "rollback":
                        conn.rollback()
                    else:
                        conn.close()
                except Exception as exc:
                    error = _failure(exc, phase, phase_started_ns)
                    if failure is None:
                        failure = error
                    else:
                        cleanup_errors.append(error)
    result["elapsed_ms"] = _elapsed_ms(started_ns)
    if failure is not None:
        return {**result, **failure, "cleanup_errors": cleanup_errors}
    return {**result, "phase": "complete", "status": "pass"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect", action="store_true", help="perform one read-only connection probe")
    args = parser.parse_args()
    result = probe(args.connect)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] in {"pass", "configuration_pass_connection_not_tested"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
