"""Run the actual permission engine on disposable CI PostgreSQL only."""
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

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


def binding_contract():
    """Synthetic driver metadata tests; not a live Neon/TLS integration test."""
    local_params = dict(host='localhost', hostaddr='127.0.0.1')
    local_info = SimpleNamespace(host='localhost', hostaddr='127.0.0.1', port=5432,
                                 get_parameters=lambda: local_params)
    local = SimpleNamespace(info=local_info, execute=lambda *args: SimpleNamespace(
        fetchone=lambda: (TARGET.database, TARGET.session_owner, TARGET.owner)))
    engine.identity(local, TARGET)
    local_info.hostaddr = '192.0.2.1'
    reject(lambda: engine.identity(local, TARGET), 'NEON_BINDING_REQUIRED')
    local_info.hostaddr = '127.0.0.1'
    for key, value in (('hostaddr', '192.0.2.1'), ('options', 'endpoint=ep-other'),
                       ('host', 'localhost,other')):
        local_params[key] = value
        reject(lambda: engine.identity(local, TARGET), 'NEON_BINDING_REQUIRED')
        local_params.clear()
        local_params['host'] = 'localhost'
    binding = engine.NeonBinding('test-project', 'br-test', 'ep-test',
                                 'ep-test.c-5.eu-central-1.aws.neon.tech')
    params = dict(host=binding.host, hostaddr='192.0.2.10', sslmode='verify-full', gssencmode='disable')
    rows = [('neon.project_id', binding.project_id, 'postmaster', 'configuration file', binding.project_id, False),
            ('neon.branch_id', binding.branch_id, 'postmaster', 'configuration file', binding.branch_id, False),
            ('neon.endpoint_id', binding.endpoint_id, 'superuser', 'configuration file', binding.endpoint_id, False)]
    fake = SimpleNamespace(info=SimpleNamespace(host=binding.host, hostaddr='192.0.2.10', port=5432,
                           get_parameters=lambda: params),
                           execute=lambda *args: SimpleNamespace(fetchall=lambda: rows))
    engine.neon_identity(fake, binding)
    for field, value, code in (
        ('host', 'ep-other.c-5.eu-central-1.aws.neon.tech', 'NEON_CONNECTION_HOST_MISMATCH'),
        ('sslmode', 'require', 'NEON_VERIFIED_TLS_REQUIRED'),
        ('gssencmode', 'prefer', 'NEON_VERIFIED_TLS_REQUIRED'),
        ('options', 'endpoint=ep-other', 'NEON_ROUTING_OVERRIDE_REFUSED'),
        ('hostaddr', '127.0.0.1', 'NEON_ROUTING_OVERRIDE_REFUSED'),
    ):
        original = params.copy()
        params[field] = value
        reject(lambda: engine.neon_identity(fake, binding), code)
        params.clear()
        params.update(original)
    for index in range(3):
        original = rows[index]
        for column, value in ((1, 'wrong-id'), (2, 'user'), (3, 'session'),
                              (4, 'wrong-reset'), (5, True)):
            changed = list(original)
            changed[column] = value
            rows[index] = tuple(changed)
            reject(lambda: engine.neon_identity(fake, binding), 'NEON_SERVER_IDENTITY_MISMATCH')
        rows[index] = original
    rows.pop()
    reject(lambda: engine.neon_identity(fake, binding), 'NEON_SERVER_IDENTITY_MISSING')
    return binding


def main():
    binding = binding_contract()
    with connection() as conn:
        # Same real PostgreSQL connection, but no Neon extension: custom GUC
        # placeholders cannot authenticate a clone as the Neon server.
        with conn.transaction():
            for name, value in (('neon.project_id', binding.project_id),
                                ('neon.branch_id', binding.branch_id),
                                ('neon.endpoint_id', binding.endpoint_id)):
                conn.execute('SELECT set_config(%s,%s,true)', (name, value))
            reject(lambda: engine.neon_server_identity(conn, binding), 'NEON_SERVER_IDENTITY_MISSING')
        owner(conn)
        reject(lambda: engine.identity(conn, replace(TARGET, recipient='postgres')), 'NEON_BINDING_REQUIRED')
        reject(lambda: engine.identity(conn, replace(TARGET, neon=binding)), 'NEON_CONNECTION_HOST_MISMATCH')
        conn.execute('RESET ROLE')
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
    print('NATIVE_PERMISSION_NEON_BINDING_CONTRACT_PASS')


if __name__ == '__main__':
    main()
