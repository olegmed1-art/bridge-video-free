"""Read-only binding of the registry workflow credential to inspected tables.

No workflow pause exemption, maintenance lock or permission grant is issued.
The original writer's URI is inspected before reconstructing a read-only session.
"""
from dataclasses import asdict
import json
import os

from database import native_cli_permission_engine as engine
from ops.native_permission_hold_guard import EXPECTED_TARGET
from ops.native_maintenance_store_runner import source_check
from ops.native_maintenance_owner_attest import failure_reason

TABLES = ('recovery_checkpoint', 'recovery_verification')
URI_KEYS = {'host', 'port', 'dbname', 'user', 'password', 'sslmode',
            'channel_binding', 'connect_timeout', 'application_name'}
PHASE = 'startup'


def parameters(raw):
    from psycopg.conninfo import conninfo_to_dict
    engine.check(type(raw) is str and 0 < len(raw) <= 8192, 'REGISTRY_CREDENTIAL_SIZE')
    value = raw.strip()  # Same trimming as record_recovery_evidence.normalize_dsn.
    engine.check(value.startswith(('postgresql://', 'postgres://')), 'REGISTRY_URI_REQUIRED')
    original = conninfo_to_dict(value)
    host = EXPECTED_TARGET['neon']['host']
    pooler = host.replace('.c-5.', '-pooler.c-5.')
    engine.check(set(original) <= URI_KEYS and original.get('host') in (host, pooler)
                 and original.get('port', '5432') == '5432'
                 and original.get('dbname') == 'neondb'
                 and original.get('user') == EXPECTED_TARGET['session_owner']
                 and bool(original.get('password'))
                 and original.get('sslmode') in ('require', 'verify-full')
                 and original.get('channel_binding') == 'require', 'REGISTRY_DESTINATION_OR_OPTIONS')
    # Never inherit a service/hostaddr/role/options override from the environment.
    engine.check(not any(k.startswith('PG') for k in os.environ), 'REGISTRY_LIBPQ_ENVIRONMENT')
    return dict(host=original['host'], port=5432, dbname='neondb',
                user=original['user'], password=original['password'],
                sslmode='verify-full', sslrootcert='/etc/ssl/certs/ca-certificates.crt',
                channel_binding='require', gssencmode='disable', connect_timeout=10,
                application_name='native-registry-scope-read-only',
                options='')


def connection_identity(conn, target, host):
    """Registry-only observer: preserve a verified direct OR pooler hostname."""
    allowed = {b'host', b'hostaddr', b'options', b'sslmode', b'gssencmode', b'channel_binding'}
    params = {r.keyword.decode('ascii'): r.val.decode('utf-8') for r in conn.pgconn.info
              if r.keyword in allowed and r.val is not None}
    engine.check(conn.info.host == host and conn.info.port == 5432 and params.get('host') == host
                 and params.get('sslmode') == 'verify-full' and params.get('gssencmode') == 'disable'
                 and params.get('channel_binding') == 'require' and not params.get('options')
                 and params.get('hostaddr', conn.info.hostaddr) == conn.info.hostaddr,
                 'REGISTRY_CONNECTION_IDENTITY')
    # The permission engine remains direct-only. Reuse only its server tag
    # verifier here; pooler support does not confer permission-session authority.
    engine.neon_server_identity(conn, target.neon)


CLOSURE = """
WITH target AS (
 SELECT c.oid,c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,a.amname
 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
 LEFT JOIN pg_catalog.pg_am a ON a.oid=c.relam
 WHERE n.nspname='public' AND c.relname=ANY(%s)
), expressions AS (
 SELECT 'pg_attrdef'::regclass::oid AS classid,oid FROM pg_catalog.pg_attrdef
 WHERE adrelid IN (SELECT oid FROM target)
 UNION ALL SELECT 'pg_constraint'::regclass::oid,oid FROM pg_catalog.pg_constraint
 WHERE conrelid IN (SELECT oid FROM target)
), dependencies AS (
 SELECT d.* FROM expressions e JOIN pg_catalog.pg_depend d
 ON d.classid=e.classid AND d.objid=e.oid
)
SELECT
 (SELECT jsonb_agg(jsonb_build_array(relname,relkind,relrowsecurity,relforcerowsecurity,amname)
                    ORDER BY relname) FROM target),
 (SELECT count(*) FROM pg_catalog.pg_trigger WHERE tgrelid IN (SELECT oid FROM target) AND NOT tgisinternal),
 (SELECT count(*) FROM pg_catalog.pg_rewrite WHERE ev_class IN (SELECT oid FROM target)),
 (SELECT count(*) FROM pg_catalog.pg_policy WHERE polrelid IN (SELECT oid FROM target)),
 (SELECT count(*) FROM pg_catalog.pg_inherits WHERE inhrelid IN (SELECT oid FROM target)
                                             OR inhparent IN (SELECT oid FROM target)),
 (SELECT count(*) FROM pg_catalog.pg_index WHERE indrelid IN (SELECT oid FROM target)
                                           AND (indexprs IS NOT NULL OR indpred IS NOT NULL)),
 (SELECT count(*) FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
   JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace
   WHERE c.conrelid IN (SELECT oid FROM target) AND n.nspname='autopilot'),
 (SELECT count(*) FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
   JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace
   WHERE a.attrelid IN (SELECT oid FROM target) AND a.attnum>0 AND NOT a.attisdropped
     AND n.nspname<>'pg_catalog'),
 (SELECT count(*) FROM dependencies d JOIN pg_catalog.pg_proc p
   ON d.refclassid='pg_proc'::regclass AND p.oid=d.refobjid
   JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname<>'pg_catalog'),
 (SELECT count(*) FROM dependencies d JOIN pg_catalog.pg_operator p
   ON d.refclassid='pg_operator'::regclass AND p.oid=d.refobjid
   JOIN pg_catalog.pg_namespace n ON n.oid=p.oprnamespace WHERE n.nspname<>'pg_catalog'),
 (SELECT count(*) FROM dependencies d JOIN pg_catalog.pg_class r
   ON d.refclassid='pg_class'::regclass AND r.oid=d.refobjid
   JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='autopilot'),
 (SELECT count(*) FROM pg_catalog.pg_index i CROSS JOIN LATERAL unnest(i.indclass) AS k(oid)
   JOIN pg_catalog.pg_opclass o ON o.oid=k.oid JOIN pg_catalog.pg_namespace n ON n.oid=o.opcnamespace
   WHERE i.indrelid IN (SELECT oid FROM target) AND n.nspname<>'pg_catalog')
"""


def observe(connect, raw):
    global PHASE
    target = engine.Target(**{**EXPECTED_TARGET, 'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    PHASE = 'credential_policy'
    kwargs = parameters(raw)
    PHASE = 'connection'
    with connect(**kwargs, autocommit=True) as conn:
        PHASE = 'read_only_configuration'
        conn.read_only = True
        PHASE = 'transaction_begin'
        with conn.transaction():
            PHASE = 'transaction_settings'
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='2s'")
            conn.execute("SET LOCAL search_path='pg_catalog'")
            PHASE = 'session_identity'
            engine.check(conn.execute("SELECT current_setting('transaction_read_only'),current_database(),current_user,session_user")
                         .fetchone() == ('on', target.database, target.owner, target.session_owner), 'REGISTRY_SESSION')
            PHASE = 'server_identity'
            connection_identity(conn, target, kwargs['host'])
            PHASE = 'catalog_closure'
            row = conn.execute(CLOSURE, (list(TABLES),)).fetchone()
            expected = [[name, 'r', False, False, 'heap'] for name in TABLES]
            engine.check(type(row) is tuple and len(row) == 12 and row[0] == expected
                         and all(type(x) is int and x == 0 for x in row[1:]), 'REGISTRY_CATALOG_NOT_CLOSED')
    return dict(audit='NATIVE_REGISTRY_RUNTIME_SCOPE_PASS', target=asdict(target),
                catalog_digest=engine.digest(row), table_count=2, connection_read_only=True,
                source_uri_options_restricted=True, workflow_pause_exempted=False,
                production_mutations=False)


def main():
    global PHASE
    import psycopg
    engine.check(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'REGISTRY_ACTOR')
    source = os.environ.get('EXPECTED_MAIN')
    PHASE = 'source_before'
    source_check(source)
    report = observe(psycopg.connect, os.environ.pop('NATIVE_REGISTRY_DATABASE_URL', ''))
    PHASE = 'source_after'
    source_check(source)
    print(json.dumps({**report, 'source_sha': source}, sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException as exc:
        print(json.dumps(dict(audit='NATIVE_REGISTRY_RUNTIME_SCOPE_REFUSED', phase=PHASE,
                              reason=failure_reason(exc),
                              production_mutations=False)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    entrypoint()
