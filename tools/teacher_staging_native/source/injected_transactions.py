"""No connection creation. Caller must supply two authorized idle psycopg sessions.

This adapter is source-only until an explicitly authorized isolated rehearsal.
The caller owns connection acquisition, credentials, permission and cleanup.
Never construct these dependencies from a source bundle or user artifact fields.
"""
from contextlib import contextmanager
from psycopg.pq import TransactionStatus

class InjectedTransactions:
    evidence_class='AUTHORIZED_CALLER_CONNECTION'
    def __init__(self,write_connection,read_connection):
        self.write=write_connection;self.read=read_connection;self.outcomes={}
        if write_connection is read_connection: raise ValueError('DISTINCT_SESSIONS_REQUIRED')
        if self.connection_identity(write_connection)==self.connection_identity(read_connection):
            raise ValueError('DISTINCT_BACKENDS_REQUIRED')
        self._idle(write_connection);self._idle(read_connection)
        # Same endpoint/database, separate sessions. Does not establish permission.
        if self._database(write_connection)!=self._database(read_connection):
            raise ValueError('SAME_DATABASE_REQUIRED')
    def _database(self,c): return (c.info.host,c.info.port,c.info.dbname)
    def connection_identity(self,c):
        if c.info.backend_pid<=0: raise ValueError('BACKEND_IDENTITY_UNAVAILABLE')
        return (*self._database(c),c.info.backend_pid)
    def _idle(self,c):
        if c.closed or c.broken:raise ValueError('CONNECTION_CLOSED_OR_BROKEN')
        if c.info.transaction_status!=TransactionStatus.IDLE: raise ValueError('IDLE_CONNECTION_REQUIRED')
    def begin_attempt(self,nonce):
        if nonce in self.outcomes: raise ValueError('ATTEMPT_REUSED')
        self.outcomes[nonce]='UNKNOWN'
    def outcome_for(self,nonce): return self.outcomes.get(nonce,'UNKNOWN')
    @contextmanager
    def write_transaction(self,nonce):
        # Failed precondition occurs before sending transaction commands.
        try:self._idle(self.write);self._idle(self.read)
        except Exception:self.outcomes[nonce]='NOT_STARTED';raise
        transaction=self.write.transaction()
        try:
            transaction.__enter__()
        except BaseException:
            self.outcomes[nonce]='UNKNOWN'
            raise
        try:
            with self.write.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '15s'")
                cursor.execute("SET LOCAL lock_timeout = '2s'")
            yield self.write
        except BaseException as exc:
            try:
                transaction.__exit__(type(exc),exc,exc.__traceback__)
                self._idle(self.write)
            except BaseException:self.outcomes[nonce]='UNKNOWN';raise
            else:self.outcomes[nonce]='ROLLED_BACK';raise
        else:
            try:
                transaction.__exit__(None,None,None)
                self._idle(self.write)
            except BaseException:self.outcomes[nonce]='UNKNOWN';raise
            else:self.outcomes[nonce]='COMMITTED'
    @contextmanager
    def read_transaction(self,nonce):
        if self.outcome_for(nonce)!='COMMITTED':raise ValueError('COMMIT_ACK_REQUIRED')
        self._idle(self.read)
        with self.read.transaction():
            with self.read.cursor() as cursor:
                cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                cursor.execute("SET LOCAL statement_timeout = '15s'")
                cursor.execute("SET LOCAL lock_timeout = '2s'")
            yield self.read
        self._idle(self.read)
