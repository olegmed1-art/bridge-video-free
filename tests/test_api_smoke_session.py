"""Offline session/deadline regressions. Doubles do not qualify PostgreSQL."""
import importlib.util
import ast
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('smoke_session', ROOT/'tools/api_smoke_session.py')
session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(session)


class QueryCanceled(Exception):
    sqlstate = '57014'


class Connection:
    def __init__(self, fail_at=None, limits=True):
        self.autocommit = False
        self.read_only = False
        self.closed = 0
        self.calls = []
        self.fail_at = fail_at
        self.limits = limits
        self.applied = set()
        self.settings = {}
        self.cancel = threading.Event()

    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql):
        self.calls.append(sql)
        if len(self.calls) == self.fail_at:
            raise QueryCanceled('synthetic setup cancellation')
        if sql.startswith('SET '):
            assert self.read_only
            self.applied.add(sql.split()[1])
            self.settings[sql.split()[1]] = sql.split('=', 1)[1].strip().strip("'")
        else:
            assert self.applied == {'statement_timeout', 'lock_timeout', 'idle_in_transaction_session_timeout'}
        if sql == 'SELECT simulated_hang':
            # A separate simulated server cancels this blocked operation. Test
            # the exception/cleanup path, not real PostgreSQL timing semantics.
            if not self.cancel.wait(1):
                raise AssertionError('cancellation did not reach blocked query')
            raise QueryCanceled('synthetic server statement deadline')
    def fetchone(self):
        return {'statement_timeout': self.settings['statement_timeout'] if self.limits else '0',
                'lock_timeout':self.settings['lock_timeout'],
                'idle_timeout':self.settings['idle_in_transaction_session_timeout'],
                'read_only':'on' if self.read_only else 'off'}
    def close(self): self.closed += 1


class SessionTests(unittest.TestCase):
    def install(self, conn):
        db = SimpleNamespace(_open_connection=lambda: conn)
        session.install(db)
        return db

    def test_each_statement_in_both_request_sessions_is_bounded_and_closed(self):
        connections = [Connection(), Connection()]
        db = SimpleNamespace(_open_connection=lambda: connections.pop(0))
        session.install(db)
        saved = connections[:]
        for _ in range(2):
            with db.connect() as conn:
                conn.execute('SELECT first')
                conn.execute('SELECT second')
            self.assertEqual(conn.closed, 1)
        with self.assertRaisesRegex(RuntimeError, 'BUDGET_EXHAUSTED'):
            with db.connect(): self.fail('unexpected extra connection')
        self.assertTrue(all(len(c.calls)==6 for c in saved))

    def test_blocked_query_receives_server_cancellation_and_closes(self):
        conn = Connection(); db = self.install(conn)
        timer = threading.Timer(0.01, conn.cancel.set)
        timer.start()
        try:
            with self.assertRaises(QueryCanceled):
                with db.connect() as actual:
                    actual.execute('SELECT simulated_hang')
        finally:
            timer.cancel(); timer.join(1)
        self.assertEqual(conn.closed, 1)

    def test_actual_overview_propagates_cancel_after_connection_cleanup(self):
        tree = ast.parse((ROOT/'bridge_school_api/main.py').read_text(encoding='utf-8'))
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'overview')
        route.decorator_list = []
        class RouteConnection(Connection):
            def execute(self, sql, params=None):
                if sql.startswith('SELECT s.school_id'):
                    return super().execute('SELECT simulated_hang')
                return super().execute(sql)
        conn = RouteConnection(); db = self.install(conn)
        namespace = {'connect':db.connect, 'EXPECTED_SCHOOL':'synthetic-school'}
        exec(compile(ast.Module(body=[route], type_ignores=[]), 'actual-overview', 'exec'), namespace)
        conn.cancel.set()
        with self.assertRaises(QueryCanceled): namespace['overview']()
        self.assertEqual(conn.closed, 1)
        self.assertEqual(conn.calls[-1], 'SELECT simulated_hang')

    def test_actual_health_cancellation_becomes_503_after_cleanup(self):
        tree = ast.parse((ROOT/'bridge_school_api/main.py').read_text(encoding='utf-8'))
        route = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'healthz')
        route.decorator_list = []
        class HTTPException(Exception):
            def __init__(self, status_code, detail): self.status_code=status_code
        class HealthConnection(Connection):
            def execute(self, sql, params=None):
                if sql.startswith('SELECT current_user AS principal'):
                    return super().execute('SELECT simulated_hang')
                return super().execute(sql)
        conn=HealthConnection(); db=self.install(conn); conn.cancel.set()
        namespace={'connect':db.connect, 'EXPECTED_SCHOOL':'synthetic', 'JSONResponse':object,
                   'logger':SimpleNamespace(error=lambda *a, **k: None),
                   '_database_failure_category':lambda exc:'timeout',
                   'incident_db_configuration':lambda: {}, 'HTTPException':HTTPException}
        exec(compile(ast.Module(body=[route], type_ignores=[]), 'actual-health', 'exec'), namespace)
        with self.assertRaises(HTTPException) as caught: namespace['healthz']()
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIsInstance(caught.exception.__cause__, QueryCanceled)
        self.assertEqual(conn.closed, 1)

    def test_disabled_statement_limit_mutation_fails_before_query(self):
        source=(ROOT/'tools/api_smoke_session.py').read_text(encoding='utf-8')
        mutated=source.replace("SET statement_timeout = '3s'", "SET statement_timeout = '0'")
        self.assertNotEqual(source, mutated)
        namespace={}; exec(compile(mutated, 'disabled-timeout-mutant', 'exec'), namespace)
        conn=Connection(); db=SimpleNamespace(_open_connection=lambda:conn)
        namespace['install'](db)
        with self.assertRaisesRegex(RuntimeError, 'LIMITS_UNVERIFIED'):
            with db.connect(): self.fail('disabled timeout reached route')
        self.assertEqual(conn.closed, 1)

    def test_setup_cancellation_at_every_stage_closes_without_yield(self):
        for i in range(1, 5):
            conn=Connection(fail_at=i); db=self.install(conn)
            with self.subTest(stage=i), self.assertRaises(QueryCanceled):
                with db.connect(): self.fail('yield before setup')
            self.assertEqual(conn.closed, 1)

    def test_unverified_settings_fail_closed(self):
        conn=Connection(limits=False); db=self.install(conn)
        with self.assertRaisesRegex(RuntimeError, 'LIMITS_UNVERIFIED'):
            with db.connect(): self.fail('unbounded session exposed')
        self.assertEqual(conn.closed, 1)

    def test_client_baseexception_and_application_error_close(self):
        for error in (KeyboardInterrupt, GeneratorExit, ValueError, QueryCanceled):
            conn=Connection(); db=self.install(conn)
            with self.subTest(error=error), self.assertRaises(error):
                with db.connect(): raise error('synthetic')
            self.assertEqual(conn.closed, 1)

    def test_autocommit_rejected_and_closed(self):
        conn=Connection(); conn.autocommit=True; db=self.install(conn)
        with self.assertRaisesRegex(RuntimeError, 'REQUIRES_TRANSACTION'):
            with db.connect(): self.fail('autocommit accepted')
        self.assertEqual(conn.closed, 1)
        self.assertEqual(conn.calls, [])

    def test_close_failure_cannot_be_reported_as_success(self):
        conn=Connection(); db=self.install(conn)
        def fail(): raise RuntimeError('synthetic close failure')
        conn.close=fail
        with self.assertRaisesRegex(RuntimeError, 'close failure'):
            with db.connect(): pass

    def test_failed_connect_consumes_budget_without_retry(self):
        calls=[]
        def fail():
            calls.append(1)
            raise OSError('synthetic unavailable')
        db=SimpleNamespace(_open_connection=fail); session.install(db)
        for _ in range(2):
            with self.assertRaises(OSError):
                with db.connect(): pass
        with self.assertRaisesRegex(RuntimeError, 'BUDGET_EXHAUSTED'):
            with db.connect(): pass
        self.assertEqual(len(calls), 2)


if __name__ == '__main__': unittest.main(verbosity=2)
