"""Bounded read-only activity observations; never a drain admission or kill list."""
from collections import Counter
from datetime import datetime
import json
import os
import time

from database import native_cli_permission_engine as engine
from ops import native_maintenance_owner_attest as owner
from ops.native_permission_hold_guard import EXPECTED_TARGET
from ops.native_maintenance_store_runner import source_check

PHASE = 'context'
LIMIT = 64
INTERVAL_SECONDS = 2


def identity(conn):
    row = conn.execute('SELECT pid,backend_start FROM pg_catalog.pg_stat_activity '
                       'WHERE pid=pg_catalog.pg_backend_pid()').fetchone()
    engine.check(type(row) is tuple and len(row) == 2 and type(row[0]) is int
                 and row[0] > 0 and isinstance(row[1], datetime), 'OBSERVER_IDENTITY')
    return row


def validate_rows(rows, observer):
    engine.check(type(rows) is list and len(rows) <= LIMIT, 'ACTIVITY_LIMIT')
    result = {}
    for row in rows:
        engine.check(type(row) is tuple and len(row) == 9
                     and type(row[0]) is int and row[0] > 0
                     and isinstance(row[1], datetime)
                     and row[2] in ('light', 'owner', 'other')
                     and row[3] in ('client', 'other')
                     and row[4] in ('idle', 'active', 'idle_in_transaction', 'other')
                     and all(type(value) is bool for value in row[5:8])
                     and row[8] in ('lt1m', 'lt5m', 'lt10m', 'ge10m_or_unknown'),
                     'ACTIVITY_SHAPE')
        key = row[:2]
        engine.check(key != observer and key not in result, 'ACTIVITY_IDENTITY')
        result[key] = row[2:]
    return result


def snapshot(conn, observer):
    with conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        conn.execute("SET LOCAL statement_timeout='3s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        engine.check(conn.execute("SELECT current_setting('transaction_read_only')").fetchone()
                     == ('on',), 'READ_ONLY_REQUIRED')
        engine.check(identity(conn) == observer, 'OBSERVER_CHANGED')
        rows = conn.execute("""
            SELECT pid,backend_start,
              CASE WHEN usename=%s THEN 'light' WHEN usename=%s THEN 'owner' ELSE 'other' END,
              CASE WHEN backend_type='client backend' THEN 'client' ELSE 'other' END,
              CASE WHEN state IS NULL THEN NULL WHEN state='idle' THEN 'idle'
                   WHEN state='active' THEN 'active' WHEN state LIKE 'idle in transaction%%'
                   THEN 'idle_in_transaction' ELSE 'other' END,
              xact_start IS NOT NULL,backend_xid IS NOT NULL,backend_xmin IS NOT NULL,
              CASE WHEN now()-state_change < interval '1 minute' THEN 'lt1m'
                   WHEN now()-state_change < interval '5 minutes' THEN 'lt5m'
                   WHEN now()-state_change < interval '10 minutes' THEN 'lt10m'
                   ELSE 'ge10m_or_unknown' END
            FROM pg_catalog.pg_stat_activity
            WHERE datname=current_database() AND pid<>pg_catalog.pg_backend_pid()
              AND backend_type<>'autovacuum worker'
            ORDER BY pid,backend_start LIMIT 65
            """, (EXPECTED_TARGET['recipient'], EXPECTED_TARGET['session_owner'])).fetchall()
        engine.check(identity(conn) == observer, 'OBSERVER_CHANGED')
        return validate_rows(rows, observer)


def groups(sample):
    keys = ('user_class', 'backend_class', 'state_class', 'has_xact', 'has_xid', 'has_xmin', 'age')
    return [dict(zip(keys, values), count=count)
            for values, count in sorted(Counter(sample.values()).items())]


def summarize(before, after):
    common = before.keys() & after.keys()
    return dict(before=groups(before), after=groups(after),
                persistent=len(common), appeared=len(after.keys() - before.keys()),
                disappeared=len(before.keys() - after.keys()),
                persistent_owner=sum(before[k][0] == after[k][0] == 'owner' for k in common),
                no_admission_authority=True, production_mutations=False,
                origin_attribution=False, sample_interval_seconds=INTERVAL_SECONDS)


def observe(connect, raw):
    global PHASE
    kwargs = owner.parameters(raw)
    target = engine.Target(**{**EXPECTED_TARGET, 'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    PHASE = 'connection'
    with connect(**kwargs, autocommit=True) as conn:
        conn.read_only = True
        PHASE = 'target_identity'
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='3s'")
            engine.identity(conn, target)
            observer = identity(conn)
        PHASE = 'first_snapshot'
        before = snapshot(conn, observer)
        time.sleep(INTERVAL_SECONDS)
        PHASE = 'second_snapshot'
        after = snapshot(conn, observer)
    return summarize(before, after)


def main():
    global PHASE
    import psycopg
    engine.check(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'RERUN_ACTOR_REFUSED')
    source = os.environ.get('EXPECTED_MAIN')
    PHASE = 'source_before'
    source_check(source)
    report = observe(psycopg.connect, os.environ.pop('NATIVE_OWNER_DATABASE_URL', ''))
    PHASE = 'source_after'
    source_check(source)
    print(json.dumps(dict(report, audit='NATIVE_ACTIVITY_OBSERVED', source_sha=source), sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException as exc:
        print(json.dumps(dict(audit='NATIVE_ACTIVITY_REFUSED', phase=PHASE,
                              reason=owner.failure_reason(exc),
                              no_admission_authority=True, production_mutations=False)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    entrypoint()
