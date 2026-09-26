"""Read-only identity and schema-generation gate for two distinct Neon sources.

Run through a protected administrator path with two owner DSNs. This emits no
credentials or row data and never authorizes export, fencing or route changes.
"""
import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit

from ops.oracle_autopilot_source_preflight import connection_parameters

PROJECT = 'misty-poetry-18012774'
SOURCES = {
    'production': ('NEON_DATABASE_URL', 'br-wispy-lab-b1rq54of',
                   'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'),
    'shadow': ('AUTOPILOT_SHADOW_SOURCE_DATABASE_URL', 'br-still-tooth-b1ilkfcj',
               'ep-floral-field-b1pjs2of.c-5.eu-central-1.aws.neon.tech'),
}
SQL = """SELECT current_setting('neon.project_id',true),
 current_setting('neon.branch_id',true),current_database(),
 current_setting('server_version_num')::int,
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname='autopilot' AND c.relkind IN ('r','p','m')),
 (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='autopilot'),
 (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='autopilot_reconcile'),
 pg_database_size(current_database()),
 (SELECT coalesce(sum(pg_total_relation_size(c.oid)),0) FROM pg_class c
  JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='autopilot'
  AND c.relkind IN ('r','p','m')),
 (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE c.relkind IN ('r','p','m') AND n.nspname NOT IN
  ('autopilot','autopilot_reconcile','pg_catalog','information_schema')
  AND n.nspname NOT LIKE 'pg_toast%'),
 (SELECT count(*) FROM public.schema_migration
  WHERE migration_key ~ '(^|_)autopilot(_|$)'),
 (SELECT count(*) FROM public.schema_migration
  WHERE migration_key !~ '(^|_)autopilot(_|$)')"""


def pinned_parameters(raw, label):
    _, _, host = SOURCES[label]
    if label == 'production':
        parameters = connection_parameters(raw, 'neondb_owner')
    else:
        parsed = urlsplit(raw.strip())
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        if (parsed.scheme not in {'postgres', 'postgresql'} or parsed.hostname != host
                or parsed.port not in {None, 5432} or parsed.path != '/neondb'
                or unquote(parsed.username or '') != 'neondb_owner'
                or not parsed.password or parsed.fragment or
                set(query) != {'sslmode', 'channel_binding'} or
                query['sslmode'] not in (['require'], ['verify-full']) or
                query['channel_binding'] != ['require']):
            raise ValueError('SHADOW_SOURCE_DSN_POLICY')
        password = unquote(parsed.password)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in password):
            raise ValueError('SHADOW_SOURCE_DSN_POLICY')
        parameters = dict(host=host, port=5432, dbname='neondb', user='neondb_owner',
                          password=password, sslmode='verify-full',
                          sslrootcert='/etc/ssl/certs/ca-certificates.crt',
                          channel_binding='require', connect_timeout=10,
                          application_name='autopilot-dual-source-preflight',
                          options='-c default_transaction_read_only=on -c statement_timeout=10000')
    if parameters['host'] != host:
        raise ValueError('SOURCE_ENDPOINT_POLICY')
    return parameters


def inspect(label, parameters):
    import psycopg
    with psycopg.connect(**parameters) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute(SQL)
            (project, branch, database, version, relations, functions, reconcile,
             size, contour_size, other_relations, autopilot_ledger_rows,
             other_ledger_rows) = cursor.fetchone()
        connection.rollback()
    if (project != PROJECT or branch != SOURCES[label][1] or database != 'neondb'
            or version // 10000 != 18 or relations < 1 or functions < 1
            or (label == 'production' and reconcile < 1)
            or (label == 'shadow' and reconcile != 0)
            or contour_size <= 0 or other_relations < 1
            or autopilot_ledger_rows < 1 or other_ledger_rows < 1):
        raise ValueError('SOURCE_BRANCH_OR_GENERATION_MISMATCH')
    return {'branch_id': branch, 'relations': relations, 'functions': functions,
            'reconcile_functions': reconcile, 'database_bytes': size,
            'autopilot_relation_bytes': contour_size,
            'other_schema_relations': other_relations,
            'autopilot_ledger_rows': autopilot_ledger_rows,
            'other_ledger_rows': other_ledger_rows,
            'full_neon_database_export_allowed': False,
            'full_ledger_table_export_allowed': False}


def main():
    if (os.environ.get('GITHUB_REPOSITORY') != 'olegmed1-art/bridge-video-free'
            or os.environ.get('GITHUB_REF') != 'refs/heads/main'):
        raise ValueError('TRUSTED_MAIN_REQUIRED')
    output = {}
    for label, (name, _, _) in SOURCES.items():
        output[label] = inspect(label, pinned_parameters(os.environ.get(name, ''), label))
    print(json.dumps({'dual_source': 'READ_ONLY_IDENTIFIED', 'project_id': PROJECT,
                      'sources': output, 'export_created': False}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # A database exception may contain endpoint or credentials; never log it.
        print(json.dumps({'dual_source': 'NOT_READY', 'error_type': type(exc).__name__}))
        sys.exit(2)
