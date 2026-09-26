"""Real PG18 concurrency evidence, called only by the disposable engine fixture."""
import threading
import time

import psycopg
from psycopg import sql

from database import native_cli_permission_engine as engine
from database.native_cli_write_fence import DatabaseWriteFence
from database.fixtures.native_cli_commit_rehearsal import connection


def run(target, path, manifest_digest, guard_factory):
    before = engine.load_manifest(path, manifest_digest)['before']

    def owned(conn):
        # Parent fixture verifies the actual loopback endpoint before any mutation.
        conn.execute('SET ROLE bridge_ci_owner')
        engine.identity(conn, target)
        return conn

    def inspect(expected):
        with connection() as conn:
            engine.check(engine.inspect(owned(conn), target, path, manifest_digest) == expected,
                         'FENCE_FIXTURE_STATE_MISMATCH')

    def change(fence, rollback=False):
        with connection() as conn:
            return engine.change(owned(conn), target, path, manifest_digest, guard_factory(),
                                 rollback=rollback, database_fence=fence)

    def blocked(fence):
        with connection() as conn:
            owned(conn)
            # Check coverage for every protected relation, including callback tables.
            for _, name in fence.tables:
                try:
                    with conn.transaction():
                        conn.execute(sql.SQL('LOCK TABLE ONLY {} IN ROW EXCLUSIVE MODE NOWAIT')
                                     .format(sql.Identifier('autopilot', name)))
                except psycopg.errors.LockNotAvailable:
                    pass
                else:
                    raise AssertionError('UNPROTECTED_TABLE:' + name)
            conn.execute("SET lock_timeout='100ms'")
            try:
                conn.execute('UPDATE autopilot.native_cli_config SET enabled=enabled')
            except psycopg.errors.LockNotAvailable:
                pass
            else:
                raise AssertionError('CONFIG_WRITE_NOT_BLOCKED')

    with connection() as holder:
        with DatabaseWriteFence(owned(holder), target, before) as fence:
            names = {name for _, name in fence.tables}
            engine.check(set(engine.LOCK_TABLES) | {
                'role_dispatch_codex_delivery_proof', 'role_dispatch_codex_terminal_receipt',
                'evidence'} <= names, 'CALLBACK_TABLE_COVERAGE_MISSING')
            blocked(fence)
            change(fence)
            blocked(fence)
            with connection() as observer:
                engine.check(fence.inspect(owned(observer), path, manifest_digest) == 'AFTER',
                             'FENCED_COMMIT_NOT_VISIBLE')
            change(fence, rollback=True)
            blocked(fence)
            with connection() as observer:
                engine.check(fence.inspect(owned(observer), path, manifest_digest) == 'BEFORE',
                             'FENCED_ROLLBACK_NOT_VISIBLE')
    print('NATIVE_WRITE_FENCE_CROSS_COMMIT_PASS')

    # A queued writer can precede a second compatible SHARE request in PG's
    # wait queue. Safe bounded refusal is required; do not claim liveness here.
    state = {}
    ready = threading.Event()
    def writer():
        try:
            with connection() as conn:
                owned(conn)
                conn.execute("SET lock_timeout='8s'")
                state['pid'] = conn.info.backend_pid
                ready.set()
                conn.execute('UPDATE autopilot.native_cli_config SET enabled=enabled')
        except BaseException as exc:
            state['error'] = exc
            ready.set()
    worker = threading.Thread(target=writer, daemon=True)
    try:
        with connection() as holder:
            with DatabaseWriteFence(owned(holder), target, before) as fence:
                worker.start()
                engine.check(ready.wait(5) and 'pid' in state, 'WRITER_START_FAILED')
                deadline = time.monotonic() + 5
                while not holder.execute('SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted)',
                                         (state['pid'],)).fetchone()[0]:
                    engine.check(time.monotonic() < deadline, 'WRITER_DID_NOT_QUEUE')
                    time.sleep(0.02)
                try:
                    change(fence)
                except (psycopg.errors.LockNotAvailable, psycopg.errors.DeadlockDetected):
                    pass
                else:
                    raise AssertionError('QUEUED_WRITER_DID_NOT_REFUSE_SECOND_LOCK')
                fence.assert_held(target)
                inspect('BEFORE')
    finally:
        if worker.ident is not None:
            worker.join(10)
            engine.check(not worker.is_alive(), 'WRITER_DID_NOT_FINISH')
    engine.check('error' not in state, 'WRITER_FAILED')
    inspect('BEFORE')
    print('NATIVE_WRITE_FENCE_QUEUED_WRITER_REFUSAL_PASS')

    class LostFence(DatabaseWriteFence):
        calls = 0
        armed = False
        fail_at = 0

        def assert_held(self, checked_target):
            if self.armed:
                self.calls += 1
                if self.calls == self.fail_at:
                    self.conn.close()
            return super().assert_held(checked_target)

    for fail_at, expected, code in (
            (2, 'BEFORE', 'DATABASE_WRITE_FENCE_LOST'),
            (3, 'AFTER', 'COMMITTED_BUT_WRITE_FENCE_POSTCHECK_FAILED')):
        with connection() as holder:
            with LostFence(owned(holder), target, before) as fence:
                fence.fail_at = fail_at
                fence.armed = True
                try:
                    change(fence)
                except engine.Refused as exc:
                    engine.check(str(exc) == code, 'WRONG_FENCE_LOSS_RESULT')
                else:
                    raise AssertionError('LOST_FENCE_ACCEPTED')
        inspect(expected)
        if expected == 'AFTER':
            change(None, rollback=True)
    print('NATIVE_WRITE_FENCE_LOSS_OUTCOMES_PASS')
