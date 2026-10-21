"""Install bounded DB sessions only in the single-process CI smoke launcher.

Install before importing app: routes capture db.connect at import time. Nothing
is installed by importing this module. No persistent DB configuration is changed.
"""
from contextlib import contextmanager
from threading import Lock


def install(db):
    """Permit the one health query and one overview query; never retry either."""
    opener = db._open_connection
    lock = Lock()
    remaining = 2

    @contextmanager
    def connect():
        nonlocal remaining
        with lock:
            if remaining == 0:
                raise RuntimeError("SMOKE_CONNECTION_BUDGET_EXHAUSTED")
            remaining -= 1
        conn = opener()
        try:
            if conn.autocommit:
                raise RuntimeError("SMOKE_REQUIRES_TRANSACTION")
            # BEGIN READ ONLY precedes the first SET. Keep this transaction open
            # throughout the request so transaction-pooling cannot switch backend.
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout = '3s'")
                cur.execute("SET lock_timeout = '1s'")
                cur.execute("SET idle_in_transaction_session_timeout = '5s'")
                cur.execute("SELECT current_setting('statement_timeout') AS statement_timeout, "
                            "current_setting('lock_timeout') AS lock_timeout, "
                            "current_setting('idle_in_transaction_session_timeout') AS idle_timeout, "
                            "current_setting('transaction_read_only') AS read_only")
                if cur.fetchone() != {"statement_timeout": "3s", "lock_timeout": "1s",
                                     "idle_timeout": "5s", "read_only": "on"}:
                    raise RuntimeError("SMOKE_SESSION_LIMITS_UNVERIFIED")
            yield conn
        finally:
            # No success commit and no network round trip for rollback. Closing
            # aborts the transaction (including its SETs). Also runs on server
            # QueryCanceled, client cancellation, setup failure and BaseException.
            conn.close()

    db.connect = connect
