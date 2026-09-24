"""Read-only source credential preflight for the autopilot PostgreSQL move."""
import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit

HOST = "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech"
POOLER_HOST = HOST.replace(".c-5.", "-pooler.c-5.")
PRINCIPALS = {
    "NEON_DATABASE_URL": ("neondb_owner", None),
    "AUTOPILOT_CALLBACK_DATABASE_URL": ("autopilot_callback_login", "autopilot_callback"),
    "BRIDGE_WORKER_DATABASE_URL": ("bridge_school_worker_principal", "bridge_school_worker"),
    "BRIDGE_HEALTH_DATABASE_URL": ("bridge_school_health_principal", "bridge_school_health"),
}


def connection_parameters(raw, principal):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in (chr(39), chr(34)):
        value = value[1:-1].strip()
    parsed = urlsplit(value)
    query = parse_qs(parsed.query, keep_blank_values=True)
    user, password = unquote(parsed.username or ""), unquote(parsed.password or "")
    # Match the established worker validator's legacy Neon-host normalization.
    # The supplied host is never contacted: libpq always receives fixed HOST.
    worker_legacy_host = (principal == "bridge_school_worker_principal"
                          and (parsed.hostname or "").endswith(".neon.tech"))
    if (parsed.scheme not in {"postgres", "postgresql"} or (parsed.hostname not in {HOST, POOLER_HOST} and not worker_legacy_host)
            or parsed.port not in {None, 5432} or parsed.path != "/neondb"
            or parsed.fragment or user != principal or not password
            or any(ord(c) < 32 or ord(c) == 127 for c in user + password)
            or query.get("sslmode") not in (["require"], ["verify-full"])
            or query.get("channel_binding") != ["require"]):
        raise ValueError("SOURCE_CREDENTIAL_CONTRACT")
    # Reconstruct fixed libpq settings; never forward arbitrary URI options.
    return dict(host=HOST, port=5432, dbname="neondb", user=user, password=password,
                sslmode="verify-full", sslrootcert="/etc/ssl/certs/ca-certificates.crt",
                channel_binding="require", connect_timeout=10,
                application_name="autopilot-migration-source-preflight",
                options="-c default_transaction_read_only=on -c statement_timeout=10000 -c lock_timeout=3000")


def main():
    import psycopg
    if (os.environ.get("GITHUB_REPOSITORY") != "olegmed1-art/bridge-video-free"
            or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("TRUSTED_MAIN_REQUIRED")
    failed = False
    for name, (principal, capability) in PRINCIPALS.items():
        try:
            parameters = connection_parameters(os.environ.get(name, ""), principal)
            with psycopg.connect(**parameters) as conn:
                conn.read_only = True
                with conn.cursor() as cur:
                    cur.execute("SELECT current_user, current_database(), current_setting('transaction_read_only')")
                    if cur.fetchone() != (principal, "neondb", "on"):
                        raise ValueError("SOURCE_IDENTITY_MISMATCH")
                    cur.execute("SELECT rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                    flags = cur.fetchone()
                    if flags is None or (capability and any(flags)):
                        raise ValueError("SOURCE_ROLE_FLAGS")
                    if capability:
                        cur.execute("SELECT pg_has_role(current_user, %s, 'USAGE')", (capability,))
                        if cur.fetchone() != (True,):
                            raise ValueError("SOURCE_CAPABILITY_MISSING")
                    if principal == "neondb_owner":
                        cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND c.relkind IN ('r','p')")
                        tables = cur.fetchone()[0]
                        if tables < 1:
                            raise ValueError("SOURCE_TABLES_MISSING")
                        print(json.dumps({"source_tables": tables, "read_only": True}))
                conn.rollback()
            print(json.dumps({"credential": name, "principal": principal, "status": "PASS"}))
        except Exception as exc:
            # Never serialize exceptions: libpq errors can carry connection details.
            reason = "CONNECTION_OR_CONTRACT"
            message = str(exc).lower()
            for needle, code in (("root certificate", "TRUST_ROOT"), ("password authentication failed", "AUTHENTICATION"), ("timeout", "TIMEOUT"), ("source_credential_contract", "SOURCE_URI_POLICY")):
                if needle in message:
                    reason = code
                    break
            print(json.dumps({"credential": name, "status": "FAIL", "error_type": type(exc).__name__, "reason": reason}))
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"preflight": "FAIL", "error_type": type(exc).__name__}))
        sys.exit(1)
