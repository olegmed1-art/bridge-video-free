"""Read-only owner-path attestation; no manifest approval or mutation permit."""
from dataclasses import asdict
import json
import os

from database import native_cli_permission_engine as engine
from ops.native_permission_hold_guard import EXPECTED_TARGET
from ops.oracle_autopilot_source_preflight import connection_parameters
from ops.native_maintenance_store_runner import source_check


def parameters(raw):
    # Reuse strict credential parsing, then reconstruct EVERY libpq setting.
    parsed = connection_parameters(raw, EXPECTED_TARGET['session_owner'])
    return dict(host=EXPECTED_TARGET['neon']['host'], port=5432, dbname='neondb',
                user='neondb_owner', password=parsed['password'], sslmode='verify-full',
                sslrootcert='/etc/ssl/certs/ca-certificates.crt', channel_binding='require',
                gssencmode='disable', connect_timeout=10,
                application_name='native-maintenance-owner-attest', options='')


def observe(connect, raw):
    target = engine.Target(**{**EXPECTED_TARGET,
                             'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    with connect(**parameters(raw), autocommit=True) as conn:
        # Set read-only BEFORE the first transaction; all statements below read
        # or constrain this session. No execute/prepare/change or ACL writes.
        conn.read_only = True
        with conn.transaction():
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            engine.check(conn.execute("SELECT current_setting('transaction_read_only')").fetchone()
                         == ('on',), 'READ_ONLY_REQUIRED')
            state = engine.snapshot(conn, target)
            engine.dormant(state)
            engine.privileges(conn, target, False)
            engine.expected_after(state)  # Validate proposed six-function scope without writing.
            snapshot_digest = engine.digest(state)
    return dict(audit='NATIVE_OWNER_READ_ONLY_PASS', target=asdict(target),
                snapshot_digest=snapshot_digest, snapshot_approved=False,
                native_enabled=False, receipts=0, nonterminal_tasks=0,
                light_native_execute=0, production_mutations=False)


def main():
    import psycopg
    engine.check(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'RERUN_ACTOR_REFUSED')
    source = os.environ.get('EXPECTED_MAIN')
    source_check(source)
    report = observe(psycopg.connect, os.environ.get('NATIVE_OWNER_DATABASE_URL', ''))
    source_check(source)
    print(json.dumps({**report, 'source_sha': source}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        # Never print exception text, connection string, snapshot or private data.
        print(json.dumps({'audit': 'NATIVE_OWNER_READ_ONLY_REFUSED', 'production_mutations': False}))
        raise SystemExit(2) from None
