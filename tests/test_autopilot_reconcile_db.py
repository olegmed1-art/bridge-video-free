from contextlib import contextmanager

import pytest

from oracle_autopilot import reconcile_db as db
from oracle_autopilot.reconcile_diagnostics import error_code


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
