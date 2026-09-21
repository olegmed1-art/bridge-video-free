"""Canonical worker connection and transaction-scoped timeouts for pooled Neon."""
from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

from database.runtime_worker_preflight import normalize_dsn, EXPECTED_PRINCIPAL


def connect():
    dsn = normalize_dsn(os.environ.get('DATABASE_URL', ''))
    if not dsn:
        raise ValueError('RECONCILE_DSN_MISSING')
    conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row,
                           connect_timeout=10, application_name='autopilot-reconcile')
    try:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout = '10s'")
            identity = conn.execute(
                "SELECT current_user = %s AS principal_ok, current_database() = 'neondb' AS database_ok",
                (EXPECTED_PRINCIPAL,),
            ).fetchone()
            if not identity['principal_ok'] or not identity['database_ok']:
                raise ValueError('RECONCILE_IDENTITY_MISMATCH')
    except BaseException:
        conn.close()
        raise
    return conn


def query(conn, sql, params=(), *, read_only=False):
    # PgBouncer transaction pooling may change backends between transactions.
    # Configure bounds in the same transaction as the RPC, never at startup.
    with conn.transaction():
        if read_only:
            conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout = '10s'")
        conn.execute("SET LOCAL lock_timeout = '3s'")
        return conn.execute(sql, params).fetchall()
