"""Secret-safe diagnostic for #1911; no connection unless --connect is supplied.

Run as a module in the affected deployment's protected environment. This does
not provision credentials, expose an HTTP route, or authorize a deployment.
"""
from __future__ import annotations

import argparse
import ipaddress
import sys
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
    ("timeout", ("timeout expired", "timed out", "statement timeout", "connection timeout")),
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
    if isinstance(exc, (TimeoutError, psycopg.errors.ConnectionTimeout)):
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
        message = str(exc).lower()
    except Exception:
        message = ""
    prefix = "connection" if phase == "connect" else phase
    status = prefix + "_failed"
    if phase == "connect" and (sqlstate in {"28000", "28P01"} or "authentication failed" in message):
        status = "authentication_failed"
    elif isinstance(exc, (TimeoutError, psycopg.errors.ConnectionTimeout)) or "timeout" in message or "timed out" in message:
        status = prefix + "_timeout"
    # Categories remain hints. The phase identifies a code operation, never a
    # proven DNS/TCP/TLS/authentication substage of psycopg.connect().
    return {"status": status, "phase": phase, "phase_elapsed_ms": _elapsed_ms(started_ns),
            "exception_class": exception_class, "sqlstate": sqlstate,
            "sqlstate_present": sqlstate_present, "sqlstate_filtered": sqlstate_filtered,
            "diagnostic_markers": _diagnostic_markers(message, exc, sqlstate)}


def _runtime_metadata(params: dict) -> dict[str, object]:
    # Never copy arbitrary runtime strings or connection parameters to output.
    from psycopg import pq
    if sys.version_info[:2] != (3, 12) or psycopg.__version__ != "3.3.4":
        raise ValueError("runtime")
    impl = pq.__impl__
    if impl not in {"binary", "c", "python"}:
        raise ValueError("implementation")
    versions = (pq.version(), pq.__build_version__)
    if any(type(v) is not int or not 0 < v < 1_000_000 for v in versions):
        raise ValueError("libpq version")
    mode = params.get("sslmode")
    if mode not in {"require", "verify-full"}:
        raise ValueError("sslmode")
    return {"schema": "incident-1911-runtime-v1",
            "python": list(sys.version_info[:3]), "psycopg": "3.3.4",
            "pq_impl": impl, "libpq_runtime": versions[0], "libpq_build": versions[1],
            "sslmode": mode, "channel_binding_required": params.get("channel_binding") == "require"}


def _tcp_subtypes(exc: Exception) -> list[str]:
    try:
        message = str(exc).lower()
    except Exception:
        message = ""
    phrases = (("refused", "connection refused"),
               ("unreachable", "network is unreachable"),
               ("no_route", "no route to host"),
               ("reset", "connection reset by peer"))
    # Aggregated client errors can contain more than one hint; not wire phases.
    return [label for label, phrase in phrases if phrase in message] or ["unknown"]


def _address_family(conn) -> str:
    try:
        address = ipaddress.ip_address(conn.info.hostaddr)
        return "ipv4" if address.version == 4 else "ipv6"
    except Exception:
        return "unknown"


def probe(connect: bool = False, *, runtime_metadata: bool = False) -> dict[str, object]:
    # connected records successful return from connect(), not an open session at
    # report time. Cleanup is attempted for every established connection.
    result: dict[str, object] = {"probe": "incident-1911-v4", "connected": False}
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
    if runtime_metadata:
        try:
            metadata = _runtime_metadata(params)
        except Exception:
            return {**result, "status": "runtime_metadata_unavailable"}
        # Flush BEFORE connect, so a watchdog timeout still leaves safe metadata.
        print(json.dumps(metadata, sort_keys=True), flush=True)
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
            options="-c default_transaction_read_only=on -c statement_timeout=5000",
        )
        result["connected"] = True
        if runtime_metadata:
            result["address_family"] = _address_family(conn)
        phase = "query"
        phase_started_ns = time.monotonic_ns()
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, current_database(), current_setting('transaction_read_only')")
            row = cur.fetchone()
        if runtime_metadata:
            valid_row = isinstance(row, (tuple, list)) and len(row) == 3
            result["session_checks"] = {
                "principal_matches": bool(valid_row and row[0] == EXPECTED_PRINCIPAL),
                "database_matches": bool(valid_row and row[1] == EXPECTED_DATABASE),
                "read_only": bool(valid_row and row[2] == "on"),
            }
        if row != (EXPECTED_PRINCIPAL, EXPECTED_DATABASE, "on"):
            failure = {"status": "session_contract_failed", "phase": "query",
                       "phase_elapsed_ms": _elapsed_ms(phase_started_ns),
                       "exception_class": None, "sqlstate": None,
                       "sqlstate_present": False, "sqlstate_filtered": False,
                       "diagnostic_markers": ["unknown"]}
    except Exception as exc:
        failure = _failure(exc, phase, phase_started_ns)
        if runtime_metadata:
            failure["tcp_subtypes"] = _tcp_subtypes(exc)
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
    parser.add_argument("--runtime-metadata", action="store_true",
                        help="emit bounded client metadata before the single connection")
    args = parser.parse_args()
    result = probe(args.connect, runtime_metadata=True) if args.runtime_metadata else probe(args.connect)
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] in {"pass", "configuration_pass_connection_not_tested"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
