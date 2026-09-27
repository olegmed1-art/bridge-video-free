from contextlib import contextmanager

import pytest

from oracle_autopilot import reconcile_db as db
from oracle_autopilot.reconcile_diagnostics import error_code
from oracle_autopilot import reconcile_diagnostics as diagnostic


class Connection:
    def __init__(self, identity=None):
        self.events = []
        self.identity = identity or dict(principal_ok=True, database_ok=True)
        self.in_transaction = False
        self.closed = False

    @contextmanager
    def transaction(self):
        self.events.append('begin')
        self.in_transaction = True
        try:
            yield
        except BaseException:
            self.events.append('rollback')
            raise
        else:
            self.events.append('commit')
        finally:
            self.in_transaction = False

    def execute(self, sql, params=()):
        assert self.in_transaction
        self.events.append(sql)
        if sql == 'FAIL':
            raise RuntimeError('synthetic failure')
        return self

    def fetchone(self):
        return self.identity

    def fetchall(self):
        return [dict(action='NO_CHANGE')]

    def close(self):
        self.closed = True


def test_each_rpc_sets_timeouts_in_its_own_transaction():
    conn = Connection()
    assert db.query(conn, 'SELECT candidate', read_only=True) == [dict(action='NO_CHANGE')]
    db.query(conn, 'SELECT mutation', ('bound',))
    assert conn.events == ['begin', 'SET TRANSACTION READ ONLY',
        "SET LOCAL statement_timeout = '10s'", "SET LOCAL lock_timeout = '3s'",
        'SELECT candidate', 'commit', 'begin', "SET LOCAL statement_timeout = '10s'",
        "SET LOCAL lock_timeout = '3s'", 'SELECT mutation', 'commit']


def test_rpc_failure_rolls_back():
    conn = Connection()
    with pytest.raises(RuntimeError):
        db.query(conn, 'FAIL')
    assert conn.events[-1] == 'rollback'


@pytest.mark.parametrize('identity', [dict(principal_ok=False,database_ok=True),
                                    dict(principal_ok=True,database_ok=False)])
def test_wrong_identity_closes_connection(monkeypatch, identity):
    conn = Connection(identity)
    monkeypatch.setattr(db, 'normalize_dsn', lambda raw: 'normalized')
    monkeypatch.setattr(db.psycopg, 'connect', lambda *a, **kw: conn)
    with pytest.raises(ValueError, match='IDENTITY_MISMATCH'):
        db.connect()
    assert conn.closed


def test_connection_uses_normalizer_and_omits_startup_options(monkeypatch):
    monkeypatch.setenv('DATABASE_URL', 'original')
    def normalize(raw):
        assert raw == 'original'
        return 'canonical'
    monkeypatch.setattr(db, 'normalize_dsn', normalize)
    def connect(dsn, **kwargs):
        assert dsn == 'canonical' and 'options' not in kwargs
        assert kwargs['connect_timeout'] == 10
        return Connection()
    monkeypatch.setattr(db.psycopg, 'connect', connect)
    assert db.connect().events[-1] == 'commit'


@pytest.mark.parametrize('text,code', [
    ('unsupported startup parameter: options SECRET', 'STARTUP_PARAMETER_UNSUPPORTED'),
    ('postgresql://name:SUPERSECRET@host/db arbitrary details', 'UNCLASSIFIED'),
    ('password authentication failed SECRET', 'AUTHENTICATION_FAILED'),
])
def test_diagnostics_never_echo_exception_text(text, code):
    assert error_code(RuntimeError(text)) == code


class DiagnosticConnection(Connection):
    read_only = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def execute(self, sql, params=()):
        assert self.read_only
        super().execute(sql, params)
        self.sql = sql
        return self

    def fetchone(self):
        if self.sql == 'SELECT 1':
            return (1,)
        if 'current_user = ' in self.sql:
            return self.identity
        if 'has_schema_privilege' in self.sql:
            return (False, False, False)
        return (0,)


@pytest.mark.parametrize('identity,success', [((True, True), True), ((False, True), False)])
def test_neon_diagnostic_direct_connection_closes_and_never_forwards_uri_options(monkeypatch, identity, success):
    monkeypatch.setenv('AUTOPILOT_DB_BACKEND', 'neon')
    conn = DiagnosticConnection(identity)
    dsn = ('postgresql://bridge_school_worker_principal:test-password@'
           + diagnostic.EXPECTED_HOST + '/neondb?sslmode=require&channel_binding=require'
           '&hostaddr=192.0.2.1&options=-c%20search_path%3Devil')
    def connect(*args, **kwargs):
        assert not args  # No original URI, including routing overrides.
        assert kwargs['host'] == diagnostic.EXPECTED_HOST.replace('-pooler.', '.')
        assert kwargs['user'] == 'bridge_school_worker_principal'
        assert kwargs['dbname'] == 'neondb'
        assert kwargs['sslmode'] == 'verify-full'
        assert kwargs['channel_binding'] == 'require' and kwargs['gssencmode'] == 'disable'
        assert 'hostaddr' not in kwargs and 'evil' not in kwargs['options']
        assert 'default_transaction_read_only=on' in kwargs['options']
        return conn
    monkeypatch.setattr(diagnostic.psycopg, 'connect', connect)
    assert diagnostic.probe(dsn, 'production_gateway', gateway=True) is success
    assert conn.closed


def test_oracle_diagnostic_keeps_pinned_local_route(monkeypatch):
    monkeypatch.setenv('AUTOPILOT_DB_BACKEND', 'postgresql')
    monkeypatch.setenv('AUTOPILOT_PG_DATABASE', 'autopilot')
    conn = DiagnosticConnection((True, True))
    dsn = 'postgresql://bridge_school_worker_principal:test-password@127.0.0.1:55432/autopilot?sslmode=verify-full'
    def connect(*args, **kwargs):
        assert args == (dsn,) and 'host' not in kwargs and 'options' not in kwargs
        return conn
    monkeypatch.setattr(diagnostic.psycopg, 'connect', connect)
    assert diagnostic.probe(dsn, 'production_gateway', gateway=True)
    assert conn.closed


def test_diagnostic_connection_failure_redacts_driver_text(monkeypatch, capsys):
    monkeypatch.setenv('AUTOPILOT_DB_BACKEND', 'neon')
    def connect(*args, **kwargs):
        raise RuntimeError('postgresql://SECRET@host/db')
    monkeypatch.setattr(diagnostic.psycopg, 'connect', connect)
    dsn = ('postgresql://bridge_school_worker_principal:test-password@'
           + diagnostic.EXPECTED_HOST + '/neondb?sslmode=require&channel_binding=require')
    assert not diagnostic.probe(dsn, 'production_gateway', gateway=True)
    output = capsys.readouterr()
    assert 'UNCLASSIFIED' in output.out and 'SECRET' not in output.out + output.err
