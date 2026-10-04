"""Read-only metadata probe in the existing GitHub smoke secret context."""
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from bridge_school_api import db

INTENT = "canon-auth-db-20261004-v1"
BRANCH = "refs/heads/test/canon-auth-validation-20261004"
DEADLINE = datetime(2026, 10, 4, 18, tzinfo=timezone.utc)


def authorized(env, now):
    return (env.get("CANON_DIAGNOSTIC_INTENT") == INTENT
            and env.get("GITHUB_REPOSITORY") == "olegmed1-art/bridge-video-free"
            and env.get("GITHUB_REF") == BRANCH
            and env.get("GITHUB_EVENT_NAME") == "push"
            and env.get("GITHUB_RUN_NUMBER") == "1"
            and env.get("GITHUB_RUN_ATTEMPT") == "1"
            and now < DEADLINE)


def classify_host(host):
    if host == db.PRODUCTION_HOST:
        return "production_pooler"
    if host == db.PRODUCTION_DIRECT_HOST:
        return "production_direct"
    if host == db.PREVIEW_HOST:
        return "preview_pooler"
    if host == db.PREVIEW_DIRECT_HOST:
        return "preview_direct"
    return "unexpected"


def probe():
    result = {"schema": INTENT, "status": "configuration_rejected"}
    # Preserve the failed runner's environment, including absence of VERCEL_ENV.
    if any(k.startswith("PG") for k in os.environ):
        return result | {"status": "libpq_environment_rejected"}
    if os.environ.get("VERCEL_ENV", ""):
        return result | {"status": "unexpected_vercel_environment"}
    try:
        raw = db._unquote(os.environ.get("BRIDGE_APP_DATABASE_URL", ""))
        raw_target = classify_host(urlsplit(raw).hostname)
        dsn = db.database_dsn()
        params = conninfo_to_dict(dsn)
        target = classify_host(params.get("host"))
        checks = {
            "role_matches": params.get("user") == db.EXPECTED_PRINCIPAL,
            "database_matches": params.get("dbname") == db.EXPECTED_DATABASE,
            "port_matches": params.get("port", "5432") == "5432",
            "tls_required": params.get("sslmode") in ("require", "verify-full"),
            "channel_binding_required": params.get("channel_binding") == "require",
            "parameters_allowed": not (params.keys() - {
                "user", "password", "dbname", "host", "port", "sslmode", "channel_binding"}),
        }
        result.update(raw_target=raw_target, effective_target=target, checks=checks)
        if not all(checks.values()) or target != "production_pooler" or raw_target not in (
                "production_pooler", "production_direct"):
            return result
        with psycopg.connect(dsn, connect_timeout=8, application_name=INTENT,
                options="-c default_transaction_read_only=on -c statement_timeout=5000") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_user=%s, current_database()=%s, "
                            "current_setting('transaction_read_only')='on', rolcanlogin, "
                            "rolvaliduntil IS NULL OR rolvaliduntil>now() "
                            "FROM pg_roles WHERE rolname=current_user",
                            (db.EXPECTED_PRINCIPAL, db.EXPECTED_DATABASE))
                row = cur.fetchone()
            conn.rollback()
        result["status"] = "pass" if row == (True, True, True, True, True) else "session_contract_failed"
    except Exception as exc:
        # No raw exception, secret bytes, length, digest, fingerprint or SQL text.
        message = str(exc).lower()
        result["status"] = ("authentication_failed" if "authentication failed" in message
                            or getattr(exc, "sqlstate", None) == "28P01" else "connection_or_configuration_failed")
    return result


def main():
    if not authorized(os.environ, datetime.now(timezone.utc)):
        print(json.dumps({"schema": INTENT, "status": "intent_rejected"}))
        return 1
    result = probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
