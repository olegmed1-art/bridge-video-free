"""Read-only Neon application-role fence probe for a future Oracle cutover.

PASS only proves these three application principals lack effective privileges in
the two Autopilot schemas. It does not prove all writers are stopped: owner and
legacy/manual credentials, active sessions and the route lease require separate
checks. No database privilege is changed by this probe.
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg

SOURCE_HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
PRINCIPALS = ('autopilot_light_worker_login', 'autopilot_callback_login',
              'bridge_school_worker_principal')
QUERY = """
WITH identity AS (SELECT %s::name AS principal),
schemas AS (SELECT oid FROM pg_namespace WHERE nspname IN ('autopilot','autopilot_reconcile'))
SELECT
  (SELECT count(*) FROM schemas),
  (SELECT count(*) FROM pg_roles r, identity i WHERE r.rolname=i.principal),
  (SELECT count(*) FROM schemas n, identity i
    WHERE has_schema_privilege(i.principal,n.oid,'USAGE,CREATE')),
  (SELECT count(*) FROM pg_proc p JOIN schemas n ON n.oid=p.pronamespace, identity i
    WHERE has_function_privilege(i.principal,p.oid,'EXECUTE')),
  (SELECT count(*) FROM pg_class c JOIN schemas n ON n.oid=c.relnamespace, identity i
    WHERE c.relkind IN ('r','p','v','m','f') AND
      has_table_privilege(i.principal,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')),
  (SELECT count(*) FROM pg_class c JOIN schemas n ON n.oid=c.relnamespace, identity i
    WHERE c.relkind='S' AND has_sequence_privilege(i.principal,c.oid,'USAGE,SELECT,UPDATE'))
"""


def validate_dsn(raw: str) -> str:
    if not raw or any(ord(c) < 33 or ord(c) == 127 for c in raw):
        raise ValueError('SOURCE_DSN_INVALID')
    parsed = urlsplit(raw)
    query = parse_qs(parsed.query, strict_parsing=True, keep_blank_values=True)
    if (parsed.scheme not in {'postgres','postgresql'} or
        parsed.hostname != SOURCE_HOST or parsed.port not in {None,5432} or
        parsed.username != 'neondb_owner' or not parsed.password or
        parsed.path != '/neondb' or parsed.fragment or
        query.get('sslmode') != ['verify-full'] or
        query.get('channel_binding') != ['require'] or
        set(query) - {'sslmode','channel_binding','connect_timeout','application_name'} or
        any(len(values) != 1 for values in query.values())):
        raise ValueError('SOURCE_DSN_INVALID')
    if any(ord(c) < 32 or ord(c) == 127 for c in unquote(parsed.password)):
        raise ValueError('SOURCE_DSN_INVALID')
    return raw


def assess(rows: dict[str, tuple[int, ...]]) -> dict[str, object]:
    if set(rows) != set(PRINCIPALS):
        raise ValueError('PRINCIPAL_INVENTORY_INCOMPLETE')
    findings = {}
    for principal, row in rows.items():
        if len(row) != 6 or row[:2] != (2,1) or any(type(value) is not int or value < 0 for value in row):
            raise ValueError('SOURCE_SCHEMA_OR_ROLE_DRIFT')
        findings[principal] = {'schema':row[2], 'function':row[3],
                               'relation':row[4], 'sequence':row[5]}
    clear = all(not any(counts.values()) for counts in findings.values())
    return {'application_principals_fenced':clear, 'scope':'AUTOPILOT_SCHEMAS_ONLY',
            'full_cutover_ready':False, 'effective_privilege_counts':findings}


def main() -> int:
    dsn = validate_dsn(os.environ.get('NEON_DATABASE_URL',''))
    with psycopg.connect(dsn,connect_timeout=10,application_name='autopilot-fence-readonly') as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute('SELECT current_database(), current_user')
            if cursor.fetchone() != ('neondb','neondb_owner'):
                raise ValueError('SOURCE_IDENTITY_DRIFT')
            rows = {}
            for principal in PRINCIPALS:
                cursor.execute(QUERY,(principal,))
                rows[principal] = tuple(cursor.fetchone())
    result = assess(rows)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['application_principals_fenced'] else 2


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError,psycopg.Error) as exc:
        print(json.dumps({'application_principals_fenced':False,'full_cutover_ready':False,
                          'error_type':type(exc).__name__}),file=sys.stderr)
        sys.exit(2)
