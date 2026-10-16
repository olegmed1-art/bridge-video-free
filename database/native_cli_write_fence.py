"""Bounded table-write exclusion across permission commits, not an ACL/DDL fence.

The separate owner connection is exclusively owned by this context. External
maintenance must still exclude privileged catalog writers and all side effects
outside ordinary autopilot tables. No production entry point is provided.
"""
from psycopg import sql
from psycopg.pq import TransactionStatus

from database import native_cli_permission_engine as engine


class DatabaseWriteFence:
    def __init__(self, conn, target, approved_state):
        self.conn = conn
        self.target = target
        self.expected = sorted((t['oid'], t['name']) for t in approved_state['tables'])
        engine.check(approved_state['target'] == engine.asdict(target), 'FENCE_TARGET_MISMATCH')
        self.active = False
        self.tables = []

    def catalog(self):
        rows = self.conn.execute("""SELECT c.oid::bigint,c.relname,c.relkind,
          EXISTS(SELECT 1 FROM pg_catalog.pg_inherits i
                 WHERE i.inhrelid=c.oid OR i.inhparent=c.oid)
          FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='autopilot' ORDER BY c.oid""").fetchall()
        engine.check([(oid, name) for oid, name, _, _ in rows] == self.expected,
                     'FENCE_RELATION_DRIFT')
        engine.check(all(kind not in ('p', 'f') and not inherited
                         for _, _, kind, inherited in rows), 'FENCE_UNSUPPORTED_RELATION')
        return [(oid, name) for oid, name, kind, _ in rows if kind == 'r']

    def __enter__(self):
        engine.check(self.conn.autocommit and
                     self.conn.info.transaction_status == TransactionStatus.IDLE,
                     'FENCE_IDLE_CONNECTION_REQUIRED')
        try:
            self.conn.execute('BEGIN')
            self.conn.execute("SET LOCAL statement_timeout='5s'")
            self.conn.execute("SET LOCAL lock_timeout='1s'")
            self.conn.execute("SET LOCAL idle_in_transaction_session_timeout='60s'")
            engine.identity(self.conn, self.target)
            self.tables = self.catalog()
            engine.check(bool(self.tables), 'FENCE_EMPTY_TABLE_SET')
            self.lock_tables(self.conn)
            engine.check(self.catalog() == self.tables, 'FENCE_RELATION_DRIFT')
            self.token = self.conn.execute(
                'SELECT pg_backend_pid(),pg_current_xact_id()::text').fetchone()
            self.active = True
            self.assert_held(self.target)
            return self
        except BaseException:
            self.active = False
            if not self.conn.closed:
                self.conn.execute('ROLLBACK')
            raise

    def lock_tables(self, conn):
        # ONLY: inheritance/partitions are explicitly refused above.
        for _, name in self.tables:
            conn.execute(sql.SQL('LOCK TABLE ONLY {} IN SHARE MODE')
                         .format(sql.Identifier('autopilot', name)))

    def assert_held(self, target):
        engine.check(self.active and target == self.target and not self.conn.closed,
                     'DATABASE_WRITE_FENCE_LOST')
        engine.check(self.conn.info.transaction_status == TransactionStatus.INTRANS,
                     'DATABASE_WRITE_FENCE_LOST')
        engine.identity(self.conn, target)
        engine.check(self.conn.execute('SELECT pg_backend_pid(),pg_current_xact_id()::text')
                     .fetchone() == self.token, 'DATABASE_WRITE_FENCE_LOST')
        engine.check(self.catalog() == self.tables, 'FENCE_RELATION_DRIFT')
        held = self.conn.execute("""SELECT relation::bigint FROM pg_catalog.pg_locks
          WHERE pid=pg_backend_pid() AND locktype='relation' AND granted AND mode='ShareLock'
          AND database=(SELECT oid FROM pg_catalog.pg_database WHERE datname=current_database())
          AND relation=ANY(%s::oid[])""", ([oid for oid, _ in self.tables],)).fetchall()
        engine.check({row[0] for row in held} == {oid for oid, _ in self.tables},
                     'DATABASE_WRITE_FENCE_LOST')

    def protect_transaction(self, conn, target):
        engine.check(conn is not self.conn, 'SEPARATE_FENCE_CONNECTION_REQUIRED')
        self.assert_held(target)
        self.lock_tables(conn)

    def inspect(self, conn, path, manifest_digest):
        engine.check(conn is not self.conn, 'SEPARATE_FENCE_CONNECTION_REQUIRED')
        self.assert_held(self.target)
        result = engine.inspect(conn, self.target, path, manifest_digest)
        self.assert_held(self.target)
        return result

    def __exit__(self, exc_type, exc, tb):
        self.active = False
        if not self.conn.closed:
            # This context never writes data. Release all held table locks.
            self.conn.execute('ROLLBACK')
