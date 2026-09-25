"""Concurrent native begin on a disposable local CI database clone."""
from concurrent.futures import ThreadPoolExecutor
import os
import secrets
import threading

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb

CLONE = 'bridge_school_ci_native_canary'
ROLE = 'bridge_ci_native_canary'
BRANCH = 'codex/ci-native-canary'


def local_dsn(name, expected_user):
    value = os.environ[name]
    config = conninfo_to_dict(value)
    if (config.get('host') not in ('localhost', '127.0.0.1', '::1')
            or config.get('hostaddr', config['host']) not in ('localhost', '127.0.0.1', '::1')
            or config.get('dbname') != 'bridge_school_ci'
            or config.get('user') != expected_user):
        raise RuntimeError('EPHEMERAL_LOCAL_CI_DATABASE_REQUIRED')
    return value


def fixture(dsn):
    with psycopg.connect(dsn) as connection:
        connection.execute("UPDATE autopilot.native_cli_config SET enabled=true,cutover_at=clock_timestamp()-interval '1 hour'")
        mailbox = connection.execute("SELECT mailbox_pr FROM autopilot.role_dispatch_mailbox_registry WHERE lifecycle='ACTIVE'").fetchone()[0]
        work = connection.execute("""SELECT work_item_id FROM autopilot.register_universal_work_item(
            %s,'AUTOPILOT','REPOSITORY_REPAIR','Concurrent native canary.',%s,0,
            '{}'::jsonb,NULL,'database-test','SQL_TEST')""", ('ci-native-begin-373', mailbox)).fetchone()[0]
        probe = connection.execute('SELECT work_item_id,lease_epoch FROM autopilot.claim_project_work_probe(%s,60)',
                                   ('ci-native-probe-373',)).fetchone()
        assert probe[0] == work, probe
        task = connection.execute('SELECT task_id FROM autopilot.materialize_project_work_probe(%s,%s,%s,true,%s)',
                                  (work, 'ci-native-probe-373', probe[1], 'a' * 40)).fetchone()[0]
        claimed = connection.execute('SELECT task_id,lease_epoch FROM autopilot.claim_next_task(%s,60)',
                                     ('ci-native-worker-373',)).fetchone()
        assert claimed[0] == task
        dispatch = connection.execute('SELECT dispatch_id FROM autopilot.prepare_role_dispatch(%s,%s,%s)',
                                      (task, 'ci-native-worker-373', claimed[1])).fetchone()[0]
        published = connection.execute('SELECT claim_epoch FROM autopilot.claim_role_dispatch_outbox_v2(%s,60)',
                                       ('ci-native-publisher-373',)).fetchone()[0]
        connection.execute('SELECT autopilot.mark_role_dispatch_published(%s,%s,%s,%s,%s)',
                           (dispatch, 'ci-native-publisher-373', published, 9900374, 'b' * 64))
        assignment = connection.execute('SELECT to_jsonb(x) FROM autopilot.get_dispatch_assignment(%s) x',
                                        (dispatch,)).fetchone()[0]
        connection.execute("""INSERT INTO autopilot.native_cli_single_canary_permit(
            dispatch_id,target_pr,expected_head_sha,expires_at)
            SELECT dispatch_id,target_pr,expected_head_sha,clock_timestamp()+interval '10 minutes'
            FROM autopilot.role_dispatch_outbox WHERE dispatch_id=%s""", (dispatch,))
    return dispatch, assignment


def grant_limited_role(owner_dsn):
    with psycopg.connect(owner_dsn) as connection:
        connection.execute(sql.SQL('GRANT USAGE ON SCHEMA autopilot TO {}').format(sql.Identifier(ROLE)))
        for signature in (
            'native_cli_reserve_canary(uuid,jsonb,text)',
            'native_cli_snapshot(uuid)',
            'native_cli_begin_canary(jsonb)',
            'native_cli_canary_current(jsonb)',
            'native_cli_ack(jsonb,text,text)',
            'native_cli_finish(jsonb,text,jsonb)',
        ):
            connection.execute(sql.SQL('GRANT EXECUTE ON FUNCTION autopilot.{} TO {}').format(
                sql.SQL(signature), sql.Identifier(ROLE)))


def reserve_as_limited_role(dsn, dispatch, assignment):
    with psycopg.connect(dsn) as connection:
        assert connection.execute('SELECT session_user').fetchone()[0] == ROLE
        for signature in ('native_cli_reserve(uuid,jsonb,text)', 'native_cli_begin(jsonb)'):
            assert connection.execute('SELECT has_function_privilege(%s,%s)',
                                      (ROLE, f'autopilot.{signature}')).fetchone()[0] is False
        assert connection.execute('SELECT has_table_privilege(%s,%s,%s)',
                                  (ROLE, 'autopilot.native_cli_receipt', 'INSERT')).fetchone()[0] is False
        reserved = connection.execute('SELECT autopilot.native_cli_reserve_canary(%s,%s,%s)',
                                      (dispatch, Jsonb(assignment), BRANCH)).fetchone()[0]
        assert reserved['state'] == 'RESERVED'
        assert connection.execute('SELECT autopilot.native_cli_snapshot(%s)',
                                  (dispatch,)).fetchone()[0] == reserved
        return reserved['request']


def main():
    owner = local_dsn('DATABASE_URL', 'bridge_ci_owner')
    admin = local_dsn('ADMIN_DATABASE_URL', 'postgres')
    cloned = make_conninfo(owner, dbname=CLONE)
    password = secrets.token_urlsafe(32)
    with psycopg.connect(admin, autocommit=True) as root:
        root.execute(sql.SQL('CREATE DATABASE {} TEMPLATE bridge_school_ci').format(sql.Identifier(CLONE)))
    try:
        with psycopg.connect(admin, autocommit=True) as root:
            root.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(
                sql.Identifier(ROLE), sql.Literal(password)))
        limited = make_conninfo(cloned, user=ROLE, password=password)
        dispatch, assignment = fixture(cloned)
        grant_limited_role(cloned)
        request = reserve_as_limited_role(limited, dispatch, assignment)
        with psycopg.connect(cloned) as connection:
            connection.execute("""UPDATE autopilot.native_cli_single_canary_permit
                SET expires_at=created_at+interval '1 microsecond' WHERE dispatch_id=%s""", (dispatch,))
        with psycopg.connect(limited) as connection:
            assert connection.execute('SELECT autopilot.native_cli_canary_current(%s)',
                                      (Jsonb(request),)).fetchone()[0] is False
            try:
                connection.execute('SELECT autopilot.native_cli_begin_canary(%s)', (Jsonb(request),))
            except psycopg.errors.RaiseException as error:
                assert 'NATIVE_CANARY_PERMIT_INVALID' in str(error)
            else:
                raise AssertionError('EXPIRED_CANARY_BEGIN_ALLOWED')
        with psycopg.connect(cloned) as connection:
            connection.execute("""UPDATE autopilot.native_cli_single_canary_permit
                SET expires_at=clock_timestamp()+interval '10 minutes' WHERE dispatch_id=%s""", (dispatch,))
        barrier = threading.Barrier(6)

        def begin(_):
            with psycopg.connect(limited, options='-c statement_timeout=10000') as connection:
                barrier.wait(timeout=8)
                try:
                    return connection.execute('SELECT autopilot.native_cli_begin_canary(%s)',
                                              (Jsonb(request),)).fetchone()[0]
                except psycopg.errors.RaiseException as error:
                    if 'NATIVE_CANARY_PERMIT_INVALID' not in str(error):
                        raise
                    return False

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(begin, range(6)))
        assert results.count(True) == 1 and results.count(False) == 5, results
        with psycopg.connect(cloned) as connection:
            state = connection.execute("""SELECT r.submission_started_at IS NOT NULL,
                p.started_at IS NOT NULL FROM autopilot.native_cli_receipt r
                JOIN autopilot.native_cli_single_canary_permit p USING(dispatch_id)
                WHERE r.dispatch_id=%s""", (dispatch,)).fetchone()
            assert state == (True, True), state
            connection.execute('UPDATE autopilot.native_cli_single_canary_permit SET revoked=true WHERE dispatch_id=%s',
                               (dispatch,))
        with psycopg.connect(limited) as connection:
            assert connection.execute('SELECT autopilot.native_cli_canary_current(%s)',
                                      (Jsonb(request),)).fetchone()[0] is False
        print('native one-shot restricted-login parallel begin: PASS')
    finally:
        with psycopg.connect(admin, autocommit=True) as root:
            root.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(CLONE)))
            root.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(ROLE)))


if __name__ == '__main__':
    main()
