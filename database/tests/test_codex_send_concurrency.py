"""Real PostgreSQL one-shot send tests; refuse any non-local/non-CI database."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import subprocess
import threading
import time
import uuid

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from oracle_autopilot.github_codex_command import render_command, command_sha256


def main():
    dsn = os.environ['DATABASE_URL']
    config = conninfo_to_dict(dsn)
    if (config.get('host') not in ('localhost', '127.0.0.1', '::1')
            or config.get('hostaddr', config['host']) not in ('localhost', '127.0.0.1', '::1')
            or config.get('dbname') != 'bridge_school_ci'
            or config.get('user') != 'bridge_ci_owner'):
        raise RuntimeError('EPHEMERAL_LOCAL_CI_DATABASE_REQUIRED')
    root = Path(__file__).resolve().parents[2]
    with psycopg.connect(dsn) as connection:
        connection.execute((root / 'database/tests/fixtures/codex_send_fixture.sql').read_text())
        did = connection.execute("SELECT pg_temp.codex_send_fixture('concurrent')").fetchone()[0]
        deadline_id = connection.execute("SELECT pg_temp.codex_send_fixture('deadline')").fetchone()[0]
        rollback_first_id = connection.execute("SELECT pg_temp.codex_send_fixture('rollback-first')").fetchone()[0]
        binding = connection.execute('SELECT autopilot.codex_command_send_binding(%s)', (did,)).fetchone()[0]
    digest = command_sha256(render_command(binding))
    barrier = threading.Barrier(8)
    attempts = [uuid.uuid4() for _ in range(8)]

    def claim(attempt, *, wait=True):
        with psycopg.connect(dsn, options='-c statement_timeout=10000') as connection:
            if wait:
                barrier.wait(timeout=10)
            granted = connection.execute(
                'SELECT autopilot.claim_codex_command_send(%s,%s,%s,%s)',
                (did, attempt, Jsonb(binding), digest)).fetchone()[0]
        # Only return after COMMIT. A lost transaction response grants nothing.
        return granted

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, attempts))
    assert results.count(True) == 1 and results.count(False) == 7, results
    winner = attempts[results.index(True)]
    # Also models a lost successful claim reply: neither the same attempt nor
    # a fresh attempt can recover POST authority by looking up the ledger.
    assert claim(winner, wait=False) is False
    assert claim(uuid.uuid4(), wait=False) is False

    # Expiry while waiting for an authority row lock must use wall clock, not
    # transaction-start now(). No send right survives the 180-second margin.
    with psycopg.connect(dsn) as setup:
        setup.execute("UPDATE autopilot.role_dispatch_outbox SET delivery_deadline_at=clock_timestamp()+interval '182 seconds' WHERE dispatch_id=%s", (deadline_id,))
        expiring = setup.execute('SELECT autopilot.codex_command_send_binding(%s)', (deadline_id,)).fetchone()[0]
    assert expiring is not None
    started = threading.Event()

    def expired_claim():
        with psycopg.connect(dsn, options='-c statement_timeout=10000') as connection:
            started.set()
            return connection.execute('SELECT autopilot.claim_codex_command_send(%s,%s,%s,%s)',
                (deadline_id, uuid.uuid4(), Jsonb(expiring), digest)).fetchone()[0]

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(dsn) as locker:
            locker.execute('SELECT dispatch_id FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s FOR UPDATE', (deadline_id,))
            future = pool.submit(expired_claim)
            assert started.wait(timeout=5)
            time.sleep(2.2)
        assert future.result(timeout=10) is False

    # Claim-first: rollback must wait for the caller's transaction, and retain
    # its ledger after commit. Test on real concurrent backend connections.
    rollback_sql = '\n'.join(line for line in (
        root / 'database/rollbacks/0370_autopilot_codex_send_intent.sql').read_text().splitlines()
        if not line.startswith('\\') and line not in ('BEGIN;', 'COMMIT;'))
    blocked_pid = []
    blocked_started = threading.Event()

    def rollback():
        with psycopg.connect(dsn, options='-c statement_timeout=10000') as connection:
            blocked_pid.append(connection.info.backend_pid)
            blocked_started.set()
            connection.execute(rollback_sql)

    def await_advisory_wait(pid):
        until = time.monotonic()+5
        with psycopg.connect(dsn, autocommit=True) as observer:
            while time.monotonic()<until:
                if observer.execute("SELECT EXISTS(SELECT FROM pg_locks WHERE pid=%s AND locktype='advisory' AND NOT granted)", (pid,)).fetchone()[0]:
                    return
                time.sleep(.02)
        raise AssertionError('CONCURRENT_BACKEND_DID_NOT_WAIT_ON_FENCE')

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(dsn) as holder:
            # A denied replay still holds the shared fence until COMMIT.
            assert holder.execute('SELECT autopilot.claim_codex_command_send(%s,%s,%s,%s)',
                (did, winner, Jsonb(binding), digest)).fetchone()[0] is False
            rollback_future = pool.submit(rollback)
            assert blocked_started.wait(timeout=5)
            await_advisory_wait(blocked_pid[0])
            assert not rollback_future.done()
        rollback_future.result(timeout=10)
    subprocess.run(['bash', 'database/scripts/migrate.sh'], cwd=root, check=True)
    assert claim(winner, wait=False) is False

    # Rollback-first: a caller already running but waiting on the shared fence
    # must observe the deleted migration marker and return false, not consume.
    with psycopg.connect(dsn) as reader:
        rb_binding = reader.execute('SELECT autopilot.codex_command_send_binding(%s)', (rollback_first_id,)).fetchone()[0]
    blocked_pid.clear()
    blocked_started.clear()

    def wait_then_claim():
        with psycopg.connect(dsn, options='-c statement_timeout=10000') as connection:
            blocked_pid.append(connection.info.backend_pid)
            blocked_started.set()
            return connection.execute('SELECT autopilot.claim_codex_command_send(%s,%s,%s,%s)',
                (rollback_first_id, uuid.uuid4(), Jsonb(rb_binding), digest)).fetchone()[0]

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(dsn) as holder:
            holder.execute("SELECT pg_advisory_xact_lock(hashtextextended('autopilot.codex_command_send_intent.v1',0))")
            claim_future = pool.submit(wait_then_claim)
            assert blocked_started.wait(timeout=5)
            await_advisory_wait(blocked_pid[0])
            holder.execute(rollback_sql)
        assert claim_future.result(timeout=10) is False
    subprocess.run(['bash', 'database/scripts/migrate.sh'], cwd=root, check=True)
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT count(*) FROM autopilot.codex_command_send_intent WHERE dispatch_id=%s', (did,)).fetchone()[0] == 1
        assert connection.execute('SELECT status FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s', (did,)).fetchone()[0] == 'PUBLISHED'
        assert connection.execute('SELECT count(*) FROM autopilot.codex_command_send_intent WHERE dispatch_id=%s', (rollback_first_id,)).fetchone()[0] == 0
    print('PASS: 8 concurrent claimants / 1 grant; no replay; deadline fencing; retained rollback ledger; no fabricated ACK')


if __name__ == '__main__':
    main()
