"""Real disposable PG18 session + journals, with EXPLICIT fake external authority.

GitHub, run, lifetime, HOLD and operator coordination are simulated. This fixture
proves database/journal composition only; it never authorizes production.
"""
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
import tempfile
from unittest.mock import patch

from database import native_cli_permission_engine as engine
from database import native_cli_maintenance_session as session
from database.fixtures.native_cli_commit_rehearsal import connection, LOGIN, PARENT, snapshot
from database.fixtures.native_maintenance_session_rehearsal import TARGET, ROUTE, owned, CONNECTIONS
from database.fixtures.native_route_drain_rehearsal import setup, prove_busy
from ops import native_maintenance_executor as executor
from ops import native_maintenance_workflow_api as api
from ops.native_maintenance_workflow_pause import Journal, Refused, digest

SOURCE = 'a' * 40
ROW = dict(id=7, path='.github/workflows/ci-only-writer.yml', state='active',
           updated_at='2026-09-26T00:00:00Z')
PLAN = dict(version=1, repository=api.REPOSITORY, source=SOURCE, workflows=[ROW])


@dataclass
class CIHold:
    name: str = 'CI_ONLY_NOT_A_HOST_IDENTITY'


class CIAuthority:
    """Not a runtime authority. All infrastructure coordination is simulated."""
    source = SOURCE
    run_id, attempt, job_id = 11, 1, 22
    def assert_running(self): pass
    def assert_alive(self): pass
    def assert_held(self, scope): pass
    def assert_drained(self, scope): pass


class CIHoldGuard:
    def __init__(self, target, operation, identity, writer_guard):
        self.writer_guard = writer_guard
    def assert_held(self, target, operation):
        engine.check(target == TARGET, 'CI_TARGET')
        self.writer_guard.assert_held(target, operation)


class CIRemote:
    def __init__(self):
        self.row, self.puts = dict(ROW), []
    def request(self, method, path):
        if path == '/git/ref/heads/main':
            return {'ref': 'refs/heads/main', 'object': {'type': 'commit', 'sha': SOURCE}}
        if method == 'GET':
            return {**self.row, 'url': api.BASE + '/actions/workflows/7'}
        action = path.rsplit('/', 1)[-1]
        self.puts.append(action)
        self.row['state'] = 'disabled_manually' if action == 'disable' else 'active'
        self.row['updated_at'] = '2026-09-26T00:00:01Z' if action == 'disable' else '2026-09-26T00:00:02Z'


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
        with tempfile.TemporaryDirectory(prefix='native-executor-ci-') as temporary:
            base = Path(temporary)
            route = base / 'route'
            setup(route)
            manifest = base / 'manifest.json'
            with owned() as conn:
                manifest_digest = engine.prepare(conn, TARGET, engine.digest(engine.snapshot(conn, TARGET)), manifest)

            def observed(expected):
                with owned() as conn:
                    engine.check(engine.inspect(conn, TARGET, manifest, manifest_digest) == expected, 'CI_DB_STATE')
                engine.check(all(c.closed for c in CONNECTIONS), 'CI_CONNECTION_LEAK')

            # Four real sessions: apply, rollback, apply with lost return, rollback.
            for index, (operation, lost) in enumerate([('apply', False), ('rollback', False),
                                                      ('apply', True), ('rollback', False)]):
                remote, authority = CIRemote(), CIAuthority()
                paths = [base / f'{index}-pause', base / f'{index}-operation']
                for path in paths:
                    path.mkdir(mode=0o700)
                journals = [Journal(path) for path in paths]
                expected = 'BEFORE' if operation == 'rollback' else 'AFTER'
                def build():
                    return executor.MaintenanceExecutor(
                        target=TARGET, operation=operation, manifest_path=manifest,
                        manifest_digest=manifest_digest, expected_route=ROUTE, approved_hold=CIHold(),
                        workflow_plan=PLAN, plan_digest=digest(PLAN), api_token='CI-only',
                        pause_journal=journals[0], operation_journal=journals[1],
                        run=authority, operator=authority, lifetime=authority)
                actual_change = engine.change
                def change(*args, **kwargs):
                    prove_busy(route)  # Actual routed client blocked while actual SQL executes.
                    return actual_change(*args, **kwargs)
                def perform(*args, **kwargs):
                    engine.check(journals[1].records[-1]['event']['kind'] == 'SESSION_INTENT', 'CI_INTENT_ORDER')
                    engine.check(remote.row['state'] == 'disabled_manually', 'CI_PAUSE_ORDER')
                    result = session.execute(*args, **kwargs)
                    if lost:
                        raise ConnectionError('CI_LOST_RETURN_AFTER_REAL_COMMIT')
                    return result
                try:
                    with ExitStack() as stack:
                        stack.enter_context(patch.object(executor, 'HoldMaintenanceGuard', CIHoldGuard))
                        stack.enter_context(patch.object(api.Transport, 'request', side_effect=remote.request))
                        stack.enter_context(patch.object(engine, 'change', side_effect=change))
                        sql = stack.enter_context(patch.object(executor, 'permission_session', side_effect=perform))
                        ex = build()
                        if lost:
                            try:
                                ex.execute(owned, route_root=route)
                            except executor.ExecutionError as exc:
                                engine.check(exc.outcome == 'UNKNOWN', 'CI_LOST_RETURN_CLASSIFICATION')
                            else:
                                raise AssertionError('CI_LOST_RETURN_ACCEPTED')
                        else:
                            engine.check(ex.execute(owned, route_root=route) == expected, 'CI_RESULT')
                        observed(expected)
                        engine.check(remote.puts == ['disable'], 'CI_AUTO_RESTORE')
                        for journal in journals:
                            journal.close()
                        journals = [Journal(path) for path in paths]
                        authority.run_id += 1
                        authority.job_id += 1
                        ex = build()
                        try:
                            ex.execute(owned, route_root=route)
                        except Refused:
                            pass
                        else:
                            raise AssertionError('CI_SESSION_REPLAYED')
                        engine.check(sql.call_count == 1, 'CI_SESSION_REPEATED')
                        class Release:
                            def assert_reconciled(self, scope, outcome):
                                engine.check(scope == ex.scope_digest and outcome == expected, 'CI_RELEASE_SCOPE')
                                # Independent fresh real DB observation, all owned connections closed.
                                observed(outcome)
                        ex.restore(Release(), expected)
                        engine.check(remote.puts == ['disable', 'enable'], 'CI_RESTORE_COUNT')
                        engine.check(ex.state == 'restored', 'CI_RESTORE_JOURNAL')
                finally:
                    for journal in journals:
                        journal.close()
            observed('BEFORE')
    finally:
        with connection() as conn:
            with conn.transaction():
                for sig in (*engine.FUNCTIONS, engine.HELPER):
                    conn.execute(f'REVOKE EXECUTE ON FUNCTION {sig} FROM {LOGIN},{PARENT} RESTRICT')
                conn.execute(f'REVOKE USAGE ON SCHEMA autopilot FROM {PARENT} RESTRICT')
                conn.execute(f'REVOKE {PARENT} FROM {LOGIN} RESTRICT')
                conn.execute(f'DROP ROLE {LOGIN}')
                conn.execute(f'DROP ROLE {PARENT}')
            engine.check(snapshot(conn) == original, 'CI_EXECUTOR_CLEANUP_MISMATCH')
    print('NATIVE_MAINTENANCE_EXECUTOR_PG18_PASS')


if __name__ == '__main__':
    main()
