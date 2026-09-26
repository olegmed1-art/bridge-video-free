"""Disposable PG18 evidence: six grants survive commit and revoke in a new session.

Not a production permission package. No native RPC or provider is invoked.
Table locks deliberately do not claim to exclude privileged ACL/schema changes.
"""
import os
from contextlib import contextmanager

import psycopg

DSN = "postgresql://postgres:postgres@localhost:5432/bridge_school_ci"
LOGIN = "native_commit_login"
PARENT = "native_commit_parent"
FUNCTIONS = (
    "autopilot.native_cli_reserve(uuid,jsonb,text)",
    "autopilot.native_cli_snapshot(uuid)",
    "autopilot.native_cli_current(jsonb)",
    "autopilot.native_cli_begin(jsonb)",
    "autopilot.native_cli_ack(jsonb,text,text)",
    "autopilot.native_cli_finish(jsonb,text,jsonb)",
)
HELPER = "autopilot.native_cli_authority_locked(uuid,jsonb)"


def require(value, code):
    if not value:
        raise RuntimeError(code)


@contextmanager
def connection():
    require(os.environ.get("ADMIN_DATABASE_URL") == DSN, "DISPOSABLE_DSN_REQUIRED")
    with psycopg.connect(DSN, autocommit=True, connect_timeout=5,
                         options="-c statement_timeout=10000 -c lock_timeout=1000") as conn:
        require(conn.execute("SELECT current_database(),session_user,current_user").fetchone()
                == ("bridge_school_ci", "postgres", "postgres"), "DISPOSABLE_IDENTITY_REQUIRED")
        yield conn


def snapshot(conn):
    return conn.execute("""
      SELECT jsonb_build_object(
        'functions',(SELECT jsonb_agg(jsonb_build_object(
          'oid',p.oid,'owner',p.proowner,'acl',p.proacl::text,
          'definition',pg_get_functiondef(p.oid)) ORDER BY p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='autopilot' AND left(p.proname,11)='native_cli_'),
        'tables',(SELECT jsonb_agg(jsonb_build_object('oid',c.oid,'owner',c.relowner,
          'acl',c.relacl::text,'columns',(SELECT jsonb_agg(jsonb_build_object(
            'number',a.attnum,'acl',a.attacl::text) ORDER BY a.attnum)
            FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped))
          ORDER BY c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='autopilot' AND c.relname IN ('native_cli_config','native_cli_receipt')),
        'schema',(SELECT jsonb_build_object('owner',nspowner,'acl',nspacl::text)
          FROM pg_namespace WHERE nspname='autopilot'),
        'roles',(SELECT jsonb_agg(jsonb_build_object('name',rolname,'login',rolcanlogin,
          'superuser',rolsuper,'inherit',rolinherit,'create_role',rolcreaterole,
          'create_db',rolcreatedb,'bypass_rls',rolbypassrls) ORDER BY rolname)
          FROM pg_roles WHERE rolname IN ('native_commit_login','native_commit_parent')),
        'membership',(SELECT jsonb_agg(to_jsonb(m) ORDER BY m.roleid,m.member,m.grantor)
          FROM pg_auth_members m WHERE m.member IN
          (SELECT oid FROM pg_roles WHERE rolname IN ('native_commit_login','native_commit_parent'))),
        'config',(SELECT jsonb_agg(to_jsonb(c)) FROM autopilot.native_cli_config c),
        'receipts',(SELECT count(*) FROM autopilot.native_cli_receipt))
    """).fetchone()[0]


def privileges(conn, expected):
    for signature in FUNCTIONS:
        require(conn.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                             (LOGIN, signature)).fetchone()[0] is expected, "RPC_ACL_MISMATCH")
    require(not conn.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                             (LOGIN, HELPER)).fetchone()[0], "HELPER_ACCESS")
    for table in ("autopilot.native_cli_config", "autopilot.native_cli_receipt"):
        require(not conn.execute("""SELECT
          has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
          OR has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES')""",
          (LOGIN, table, LOGIN, table)).fetchone()[0], "TABLE_ACCESS")
    require(conn.execute("SELECT has_schema_privilege(%s,'autopilot','USAGE')",
                         (LOGIN,)).fetchone()[0], "SCHEMA_USAGE_MISSING")


def owner_context(conn):
    conn.execute("SET LOCAL ROLE bridge_ci_owner")
    conn.execute("LOCK TABLE autopilot.native_cli_config, autopilot.native_cli_receipt IN SHARE ROW EXCLUSIVE MODE")
    require(conn.execute("SELECT count(*)=1 AND bool_and(NOT enabled) FROM autopilot.native_cli_config")
            .fetchone()[0] is True, "CONFIG_NOT_DISABLED")
    require(conn.execute("SELECT count(*) FROM autopilot.native_cli_receipt").fetchone()[0] == 0,
            "RECEIPTS_PRESENT")


def apply(conn, before, fail_after=False):
    with conn.transaction():
        owner_context(conn)
        require(snapshot(conn) == before, "BASELINE_DRIFT")
        privileges(conn, False)
        for signature in FUNCTIONS:  # Constants only, no caller-supplied SQL identifiers.
            conn.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {LOGIN}")
        privileges(conn, True)
        after = snapshot(conn)
        unchanged = {k: v for k, v in after.items() if k != 'functions'}
        require(unchanged == {k: v for k, v in before.items() if k != 'functions'}, "NON_ACL_CHANGE")
        if fail_after:
            raise RuntimeError("INJECTED_POSTCHECK_FAILURE")
        return after


def revoke(conn, before, after):
    with conn.transaction():
        owner_context(conn)
        require(snapshot(conn) == after, "ROLLBACK_DRIFT")
        for signature in FUNCTIONS:
            conn.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {LOGIN} RESTRICT")
        privileges(conn, False)
        require(snapshot(conn) == before, "ROLLBACK_MISMATCH")


def expect_error(action, code):
    try:
        action()
    except RuntimeError as exc:
        require(str(exc) == code, "UNEXPECTED_REJECTION")
    else:
        raise RuntimeError("EXPECTED_REJECTION_MISSING")


def main():
    with connection() as conn:
        require(conn.execute("SELECT count(*) FROM pg_roles WHERE rolname IN (%s,%s)",
                             (LOGIN, PARENT)).fetchone()[0] == 0, "FIXTURE_ROLE_EXISTS")
        original = snapshot(conn)
        with conn.transaction():
            conn.execute(f"CREATE ROLE {PARENT} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE")
            conn.execute(f"CREATE ROLE {LOGIN} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT")
            conn.execute(f"GRANT {PARENT} TO {LOGIN}")
            conn.execute(f"GRANT USAGE ON SCHEMA autopilot TO {PARENT}")
    try:
        with connection() as conn:
            before = snapshot(conn)
            require(len(before['functions']) == 7, "NATIVE_FUNCTION_SET_DRIFT")
            privileges(conn, False)
            expect_error(lambda: apply(conn, before, fail_after=True), "INJECTED_POSTCHECK_FAILURE")
        with connection() as conn:
            require(snapshot(conn) == before, "FAILED_TRANSACTION_LEAK")
            after = apply(conn, before)
        # A different backend verifies the committed grants, then adds independent drift.
        with connection() as conn:
            require(snapshot(conn) == after, "COMMIT_NOT_VISIBLE")
            privileges(conn, True)
            conn.execute(f"GRANT EXECUTE ON FUNCTION {HELPER} TO {PARENT}")
            drifted = snapshot(conn)
        with connection() as conn:
            expect_error(lambda: revoke(conn, before, after), "ROLLBACK_DRIFT")
            require(snapshot(conn) == drifted, "REJECTED_ROLLBACK_ERASED_DRIFT")
            conn.execute(f"REVOKE EXECUTE ON FUNCTION {HELPER} FROM {PARENT} RESTRICT")
            require(snapshot(conn) == after, "DRIFT_CLEANUP_MISMATCH")
            revoke(conn, before, after)
        with connection() as conn:
            require(snapshot(conn) == before, "COMMITTED_ROLLBACK_NOT_VISIBLE")
            privileges(conn, False)
            # Demonstrate that table locks do not exclude a privileged ACL writer.
            with conn.transaction():
                owner_context(conn)
                with connection() as other:
                    other.execute(f"GRANT EXECUTE ON FUNCTION {HELPER} TO {PARENT}")
                expect_error(lambda: apply(conn, before), "BASELINE_DRIFT")
            conn.execute(f"REVOKE EXECUTE ON FUNCTION {HELPER} FROM {PARENT} RESTRICT")
            require(snapshot(conn) == before, "CONCURRENT_PROBE_LEAK")
    finally:
        # Only these fixture roles were created after asserting they did not exist.
        with connection() as conn:
            with conn.transaction():
                for signature in (*FUNCTIONS, HELPER):
                    conn.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM {LOGIN},{PARENT} RESTRICT")
                conn.execute(f"REVOKE USAGE ON SCHEMA autopilot FROM {PARENT} RESTRICT")
                conn.execute(f"REVOKE {PARENT} FROM {LOGIN} RESTRICT")
                conn.execute(f"DROP ROLE {LOGIN}")
                conn.execute(f"DROP ROLE {PARENT}")
            require(snapshot(conn) == original, "FINAL_CLEANUP_MISMATCH")
    print("NATIVE_GRANT_CROSS_COMMIT_REHEARSAL_PASS")
    print("PRIVILEGED_ACL_WRITER_NOT_EXCLUDED_BY_TABLE_LOCKS_CONFIRMED")


if __name__ == "__main__":
    main()
