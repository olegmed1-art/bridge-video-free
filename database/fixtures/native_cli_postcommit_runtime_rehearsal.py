"""Disposable recipient calls and transient post-COMMIT catalog effects.

No production entry point. A test maintenance stub is deliberately insufficient
to exclude the privileged contender; successful drift concealment is a limit.
"""
import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from database import native_cli_permission_engine as engine
from database.native_cli_write_fence import DatabaseWriteFence
from database.fixtures.native_cli_commit_rehearsal import connection, LOGIN


def run(target, path, manifest_digest, guard_factory):
    before = engine.load_manifest(path, manifest_digest)['before']
    signature = 'autopilot.native_cli_snapshot(uuid)'
    probe_id = '00000000-0000-4000-8000-000000001977'
    request = Jsonb({'dispatch_id': probe_id})

    def owned(conn):
        conn.execute('SET ROLE bridge_ci_owner')
        engine.identity(conn, target)
        return conn

    def recipient(conn):
        conn.execute(sql.SQL('SET SESSION AUTHORIZATION {}').format(sql.Identifier(LOGIN)))
        engine.check(conn.execute('SELECT session_user,current_user').fetchone()
                     == (LOGIN, LOGIN), 'RUNTIME_SESSION_IDENTITY')
        engine.check(conn.execute('SHOW transaction_read_only').fetchone()[0] == 'off',
                     'RUNTIME_READ_ONLY_MASK')
        engine.check(conn.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
                     .fetchone()[0] is False, 'RUNTIME_SUPERUSER_MASK')
        return conn

    def table_rows(conn):
        rows = {}
        names = conn.execute("""SELECT c.relname FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname='autopilot' AND c.relkind='r' ORDER BY c.relname""").fetchall()
        for (name,) in names:
            rows[name] = conn.execute(sql.SQL(
                'SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),\'[]\'::jsonb) '
                'FROM {} t').format(sql.Identifier('autopilot', name))).fetchone()[0]
        return engine.digest(rows)

    calls = (
        ('SELECT autopilot.native_cli_reserve(%s::uuid,%s::jsonb,%s)',
         (probe_id, Jsonb({}), 'codex/dormant-probe'), 'NATIVE_RESERVATION_INVALID'),
        ('SELECT autopilot.native_cli_snapshot(%s::uuid)',
         (probe_id,), 'NATIVE_RESERVATION_NOT_OWNED'),
        ('SELECT autopilot.native_cli_current(%s::jsonb)', (request,), None),
        ('SELECT autopilot.native_cli_begin(%s::jsonb)', (request,), 'NATIVE_BEGIN_NOT_OWNED'),
        ('SELECT autopilot.native_cli_ack(%s::jsonb,%s,%s)',
         (request, 'task_dormant_probe', 'a' * 64), 'NATIVE_ACK_INVALID'),
        ('SELECT autopilot.native_cli_finish(%s::jsonb,%s,%s::jsonb)',
         (request, 'task_dormant_probe', Jsonb({})), 'NATIVE_TERMINAL_INVALID'),
    )
    with connection() as conn:
        owned(conn)
        original_definition = conn.execute('SELECT pg_get_functiondef(%s::regprocedure)',
                                           (signature,)).fetchone()[0]
        engine.check(conn.execute("SELECT to_regnamespace('native_postcommit_probe')")
                     .fetchone()[0] is None, 'PROBE_SCHEMA_EXISTS')
    changed = False
    schema_created = False
    try:
        with connection() as holder:
            with DatabaseWriteFence(owned(holder), target, before) as fence:
                with connection() as conn:
                    engine.change(owned(conn), target, path, manifest_digest,
                                  guard_factory(), database_fence=fence)
                    rows_before = table_rows(conn)
                for command, params, expected_error in calls:
                    with connection() as runtime:
                        recipient(runtime)
                        try:
                            value = runtime.execute(command, params).fetchone()[0]
                        except psycopg.errors.RaiseException as exc:
                            engine.check(expected_error is not None
                                         and exc.diag.message_primary == expected_error,
                                         'UNEXPECTED_DORMANT_ERROR')
                        else:
                            engine.check(expected_error is None and value is False,
                                         'UNEXPECTED_DORMANT_RESULT')
                with connection() as observer:
                    owned(observer)
                    engine.check(table_rows(observer) == rows_before, 'DORMANT_ROW_EFFECT')
                    engine.check(fence.inspect(observer, path, manifest_digest) == 'AFTER',
                                 'DORMANT_METADATA_EFFECT')
                print('NATIVE_POSTCOMMIT_EMPTY_RECEIPT_CALLS_PASS')

                # Outside the ordinary-autopilot-table fence, deliberately.
                with connection() as contender:
                    with contender.transaction():
                        contender.execute('CREATE SCHEMA native_postcommit_probe')
                        contender.execute('GRANT USAGE ON SCHEMA native_postcommit_probe TO bridge_ci_owner')
                        contender.execute('CREATE TABLE native_postcommit_probe.effect(marker boolean NOT NULL)')
                        contender.execute('ALTER TABLE native_postcommit_probe.effect OWNER TO bridge_ci_owner')
                    schema_created = True
                    contender.execute("""CREATE OR REPLACE FUNCTION autopilot.native_cli_snapshot(p_dispatch_id uuid)
                      RETURNS jsonb LANGUAGE plpgsql VOLATILE SECURITY DEFINER
                      SET search_path TO 'pg_catalog', 'autopilot' AS $probe$
                      BEGIN
                        INSERT INTO native_postcommit_probe.effect VALUES (true);
                        RETURN '{"probe":true}'::jsonb;
                      END $probe$""")
                    changed = True
                with connection() as runtime:
                    engine.check(recipient(runtime).execute(
                        'SELECT autopilot.native_cli_snapshot(%s::uuid)', (probe_id,)).fetchone()[0]
                        == {'probe': True}, 'TRANSIENT_BODY_NOT_CALLED')
                with connection() as contender:
                    contender.execute(original_definition)
                    changed = False
                with connection() as observer:
                    owned(observer)
                    engine.check(fence.inspect(observer, path, manifest_digest) == 'AFTER',
                                 'RESTORED_METADATA_NOT_EQUAL')
                    engine.check(observer.execute('SELECT count(*) FROM native_postcommit_probe.effect')
                                 .fetchone()[0] == 1, 'TRANSIENT_EFFECT_NOT_RETAINED')
                    engine.check(table_rows(observer) == rows_before, 'UNEXPECTED_AUTOPILOT_EFFECT')
                print('NATIVE_POSTCOMMIT_RESTORED_CATALOG_EFFECT_CONFIRMED')
    finally:
        with connection() as conn:
            if changed:
                conn.execute(original_definition)
            if schema_created:
                conn.execute('DROP TABLE native_postcommit_probe.effect')
                conn.execute('DROP SCHEMA native_postcommit_probe')
            owned(conn)
            state = engine.inspect(conn, target, path, manifest_digest)
            if state == 'AFTER':
                engine.change(conn, target, path, manifest_digest, guard_factory(), rollback=True)
            else:
                engine.check(state == 'BEFORE', 'POSTCOMMIT_CLEANUP_DRIFT')
        with connection() as conn:
            engine.check(engine.inspect(owned(conn), target, path, manifest_digest) == 'BEFORE',
                         'POSTCOMMIT_BASELINE_NOT_RESTORED')
