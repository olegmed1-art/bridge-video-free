"""Run the actual permission engine on disposable CI PostgreSQL only."""
import tempfile
from dataclasses import replace
from pathlib import Path

from database import native_cli_permission_engine as engine
from database.fixtures.native_cli_commit_rehearsal import (
    FUNCTIONS, HELPER, LOGIN, PARENT, connection, snapshot as fixture_snapshot,
)

TARGET = engine.Target('bridge_school_ci', 'postgres', 'bridge_ci_owner', LOGIN)


class CIGuard(engine.MaintenanceGuard):
    """Test stub only; NEVER a production maintenance implementation."""
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at

    def assert_held(self, target, operation):
        engine.check(target == TARGET, 'CI_GUARD_TARGET')
        self.calls += 1
        engine.check(self.calls != self.fail_at, 'CI_INJECTED_LOST_GUARD')


def owner(conn):
    conn.execute('SET ROLE bridge_ci_owner')
    return conn


def reject(action, code):
    try:
        action()
    except engine.Refused as exc:
        engine.check(str(exc) == code, f'EXPECTED_{code}_GOT_{exc}')
    else:
        raise RuntimeError(f'EXPECTED_REJECTION_{code}')


def main():
    with connection() as conn:
        engine.check(conn.execute('SELECT count(*) FROM pg_roles WHERE rolname IN (%s,%s)',
                                 (LOGIN, PARENT)).fetchone()[0] == 0, 'FIXTURE_ROLE_EXISTS')
        original = fixture_snapshot(conn)
        with conn.transaction():
            conn.execute(f'CREATE ROLE {PARENT} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
            conn.execute(f'CREATE ROLE {LOGIN} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT')
            conn.execute(f'GRANT {PARENT} TO {LOGIN}')
            conn.execute(f'GRANT USAGE ON SCHEMA autopilot TO {PARENT}')
    try:
        with tempfile.TemporaryDirectory(prefix='native-permissions-') as directory:
            path = Path(directory) / 'manifest.json'
            with connection() as conn:
                owner(conn)
                reference = engine.digest(engine.snapshot(conn, TARGET))
                reject(lambda: engine.prepare(conn, replace(TARGET, database='wrong'), reference, path),
                       'TARGET_IDENTITY_MISMATCH')
                reject(lambda: engine.prepare(conn, TARGET, '0' * 64, path), 'APPROVED_BASELINE_MISMATCH')
                engine.check(not path.exists(), 'REJECTED_PREPARATION_WROTE_MANIFEST')
                manifest_digest = engine.prepare(conn, TARGET, reference, path)
                reject(lambda: engine.change(conn, TARGET, path, manifest_digest, engine.MaintenanceGuard()),
                       'MAINTENANCE_IMPLEMENTATION_REQUIRED')
                reject(lambda: engine.change(conn, TARGET, path, manifest_digest, CIGuard(fail_at=3)),
                       'CI_INJECTED_LOST_GUARD')
            with connection() as conn:
                owner(conn)
                engine.check(engine.inspect(conn, TARGET, path, manifest_digest) == 'BEFORE',
                             'FAILED_TRANSACTION_LEAK')
                data = path.read_bytes()
                path.write_bytes(data + b' ')
                reject(lambda: engine.change(conn, TARGET, path, manifest_digest, CIGuard()),
                       'MANIFEST_DIGEST_MISMATCH')
                path.write_bytes(data)
            with connection() as conn:
                conn.execute('UPDATE autopilot.native_cli_config SET enabled=true')
            try:
                with connection() as conn:
                    owner(conn)
                    reject(lambda: engine.change(conn, TARGET, path, manifest_digest, CIGuard()),
                           'CONFIG_NOT_DISABLED')
            finally:
                with connection() as conn:
                    conn.execute('UPDATE autopilot.native_cli_config SET enabled=false')
            with connection() as conn:
                owner(conn)
                # Discard the return value to model missing acknowledgement. The
                # commit itself is real; transport failure is not injected here.
                engine.change(conn, TARGET, path, manifest_digest, CIGuard())
            with connection() as conn:
                owner(conn)
                engine.check(engine.inspect(conn, TARGET, path, manifest_digest) == 'AFTER',
                             'COMMITTED_STATE_NOT_DETECTED')
                reject(lambda: engine.change(conn, TARGET, path, manifest_digest, CIGuard()),
                       'BASELINE_DRIFT')
            with connection() as conn:
                conn.execute(f'GRANT EXECUTE ON FUNCTION {HELPER} TO {PARENT}')
            with connection() as conn:
                owner(conn)
                engine.check(engine.inspect(conn, TARGET, path, manifest_digest) == 'DRIFT',
                             'INTERVENING_DRIFT_NOT_DETECTED')
                reject(lambda: engine.change(conn, TARGET, path, manifest_digest, CIGuard(), rollback=True),
                       'ROLLBACK_DRIFT')
                engine.check(conn.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                                         (PARENT, HELPER)).fetchone()[0], 'DRIFT_ERASED')
            with connection() as conn:
                conn.execute(f'REVOKE EXECUTE ON FUNCTION {HELPER} FROM {PARENT} RESTRICT')
                owner(conn)
                engine.change(conn, TARGET, path, manifest_digest, CIGuard(), rollback=True)
            with connection() as conn:
                owner(conn)
                engine.check(engine.inspect(conn, TARGET, path, manifest_digest) == 'BEFORE',
                             'ORIGINAL_STATE_NOT_RESTORED')
    finally:
        with connection() as conn:
            with conn.transaction():
                for signature in (*FUNCTIONS, HELPER):
                    conn.execute(f'REVOKE EXECUTE ON FUNCTION {signature} FROM {LOGIN},{PARENT} RESTRICT')
                conn.execute(f'REVOKE USAGE ON SCHEMA autopilot FROM {PARENT} RESTRICT')
                conn.execute(f'REVOKE {PARENT} FROM {LOGIN} RESTRICT')
                conn.execute(f'DROP ROLE {LOGIN}')
                conn.execute(f'DROP ROLE {PARENT}')
            engine.check(fixture_snapshot(conn) == original, 'FINAL_CLEANUP_MISMATCH')
    print('NATIVE_PERMISSION_ENGINE_REHEARSAL_PASS')


if __name__ == '__main__':
    main()
