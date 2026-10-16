"""Disposable root/PG18 coordinator composition, with explicit external CI stub."""
from contextlib import contextmanager
import fcntl
from pathlib import Path
import tempfile
from unittest.mock import patch

from database import native_cli_permission_engine as engine
from database import native_cli_maintenance_session as session
from database.fixtures.native_cli_commit_rehearsal import (
    connection, LOGIN, PARENT, snapshot,
)
from database.fixtures.native_route_drain_rehearsal import setup, prove_busy, start_lease, finish_lease

TARGET = engine.Target('bridge_school_ci', 'postgres', 'bridge_ci_owner', LOGIN)
ROUTE = dict(version=1, backend='neon', database='autopilot', epoch=1)
CONNECTIONS = []


@contextmanager
def owned():
    with connection() as conn:
        CONNECTIONS.append(conn)
        conn.execute('SET ROLE bridge_ci_owner')
        engine.identity(conn, TARGET)
        yield conn


class CIExternalGuard(engine.MaintenanceGuard):
    """Only a fixture stub; proves no actual operator/GitHub/HOLD agreement."""
    def __init__(self, fail_at=None):
        self.calls = 0
        self.fail_at = fail_at
        self.lost = False

    def assert_held(self, target, operation):
        engine.check(target == TARGET and operation in ('apply', 'rollback'), 'CI_SCOPE')
        self.calls += 1
        engine.check(not self.lost and self.calls != self.fail_at, 'CI_EXTERNAL_LOST')


def main():
    with connection() as conn:
        engine.check(conn.execute('SELECT count(*) FROM pg_roles WHERE rolname IN (%s,%s)',
                                 (LOGIN, PARENT)).fetchone()[0] == 0, 'CI_ROLES_EXIST')
        original = snapshot(conn)
        with conn.transaction():
            conn.execute(f'CREATE ROLE {PARENT} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
            conn.execute(f'CREATE ROLE {LOGIN} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT')
            conn.execute(f'GRANT {PARENT} TO {LOGIN}')
            conn.execute(f'GRANT USAGE ON SCHEMA autopilot TO {PARENT}')
    try:
        with tempfile.TemporaryDirectory(prefix='native-route-drain-') as temporary:
            root = Path(temporary) / 'protocol'
            setup(root)
            manifest = Path(temporary) / 'manifest.json'
            with owned() as conn:
                approved = engine.digest(engine.snapshot(conn, TARGET))
                digest = engine.prepare(conn, TARGET, approved, manifest)

            def execute(**kwargs):
                return session.execute(TARGET, manifest, digest, kwargs.pop('factory', owned),
                                       ROUTE, route_root=root, **kwargs)

            def inspect(expected):
                with owned() as conn:
                    engine.check(engine.inspect(conn, TARGET, manifest, digest) == expected,
                                 'SESSION_STATE_MISMATCH')

            def released():
                engine.check(all(c.closed for c in CONNECTIONS), 'OWNED_CONNECTION_LEAK')
                client, record = start_lease(root)
                try:
                    engine.check(record.get('route') == ROUTE, 'OWNED_ROUTE_LEAK')
                finally:
                    finish_lease(client)

            try:
                execute()
            except engine.Refused as exc:
                engine.check(str(exc) == 'MAINTENANCE_IMPLEMENTATION_REQUIRED', 'WRONG_DEFAULT_REFUSAL')
            else:
                raise AssertionError('DEFAULT_COORDINATOR_ADMITTED')
            inspect('BEFORE')
            released()

            # A real route-v1 client is refused while the actual engine runs.
            actual_change = engine.change
            def with_client(*args, **kwargs):
                prove_busy(root)
                return actual_change(*args, **kwargs)
            with patch.object(engine, 'change', with_client):
                engine.check(execute(external_guard=CIExternalGuard()) == 'AFTER', 'APPLY_RESULT')
            inspect('AFTER')
            released()
            engine.check(execute(external_guard=CIExternalGuard(), rollback=True) == 'BEFORE',
                         'REVOKE_RESULT')

            try:
                execute(external_guard=CIExternalGuard(fail_at=4))
            except session.MaintenanceOutcomeError as exc:
                engine.check(exc.outcome == 'BEFORE', 'PRECOMMIT_FAILURE_CLASSIFICATION')
            else:
                raise AssertionError('PRECOMMIT_FAILURE_MISSING')
            inspect('BEFORE')
            released()

            # Model loss of the return value after a real successful commit.
            # This is not an injected network fault during COMMIT itself.
            def discard_ack(*args, **kwargs):
                actual_change(*args, **kwargs)
                raise ConnectionError('CI_LOST_RETURN_VALUE')
            with patch.object(engine, 'change', discard_ack):
                try:
                    execute(external_guard=CIExternalGuard())
                except session.MaintenanceOutcomeError as exc:
                    engine.check(exc.outcome == 'AFTER', 'POSTCOMMIT_FAILURE_CLASSIFICATION')
                else:
                    raise AssertionError('POSTCOMMIT_FAILURE_MISSING')
            inspect('AFTER')
            released()
            execute(external_guard=CIExternalGuard(), rollback=True)

            count = 0
            @contextmanager
            def observer_unavailable():
                nonlocal count
                count += 1
                if count >= 3:
                    raise ConnectionError('CI_OBSERVER_UNAVAILABLE')
                with owned() as conn:
                    yield conn
            try:
                execute(external_guard=CIExternalGuard(), factory=observer_unavailable)
            except session.MaintenanceOutcomeError as exc:
                engine.check(exc.outcome == 'UNKNOWN', 'UNKNOWN_COMMIT_MISCLASSIFIED')
            else:
                raise AssertionError('UNKNOWN_OUTCOME_MISSING')
            inspect('AFTER')  # No blind retry or revoke occurred.
            released()
            execute(external_guard=CIExternalGuard(), rollback=True)
            inspect('BEFORE')

            for fault in ('external', 'route', 'holder'):
                external = CIExternalGuard()
                def lose_resource(*args, **kwargs):
                    result = actual_change(*args, **kwargs)
                    guard = args[4]
                    if fault == 'external':
                        external.lost = True
                    elif fault == 'route':
                        fcntl.flock(guard.route.lock, fcntl.LOCK_UN)
                    else:
                        guard.fence.conn.close()
                    return result
                with patch.object(engine, 'change', lose_resource):
                    try:
                        execute(external_guard=external)
                    except session.MaintenanceOutcomeError as exc:
                        engine.check(exc.outcome == 'UNKNOWN', 'LOST_RESOURCE_NOT_UNKNOWN')
                    else:
                        raise AssertionError('LOST_RESOURCE_ADMITTED')
                inspect('AFTER')
                released()
                execute(external_guard=CIExternalGuard(), rollback=True)

            count = 0
            @contextmanager
            def cleanup_failure():
                nonlocal count
                count += 1
                ordinal = count
                with owned() as conn:
                    yield conn
                if ordinal == 1:
                    raise OSError('CI_CLEANUP_FAILURE_AFTER_CLOSE')
            try:
                execute(external_guard=CIExternalGuard(), factory=cleanup_failure)
            except session.MaintenanceOutcomeError as exc:
                engine.check(exc.outcome == 'AFTER' and exc.cleanup_failed,
                             'CLEANUP_LOST_CONFIRMED_OUTCOME')
            else:
                raise AssertionError('CLEANUP_FAILURE_NOT_REPORTED')
            inspect('AFTER')
            released()
            execute(external_guard=CIExternalGuard(), rollback=True)
            inspect('BEFORE')
            released()
    finally:
        with connection() as conn:
            with conn.transaction():
                for sig in (*engine.FUNCTIONS, engine.HELPER):
                    conn.execute(f'REVOKE EXECUTE ON FUNCTION {sig} FROM {LOGIN},{PARENT} RESTRICT')
                conn.execute(f'REVOKE USAGE ON SCHEMA autopilot FROM {PARENT} RESTRICT')
                conn.execute(f'REVOKE {PARENT} FROM {LOGIN} RESTRICT')
                conn.execute(f'DROP ROLE {LOGIN}')
                conn.execute(f'DROP ROLE {PARENT}')
            engine.check(snapshot(conn) == original, 'SESSION_FIXTURE_CLEANUP_MISMATCH')
    print('NATIVE_MAINTENANCE_COMPOSED_SESSION_PASS')


if __name__ == '__main__':
    main()
