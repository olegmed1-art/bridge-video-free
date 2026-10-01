"""Backup-only production route. No credentials or client errors reach logs.

The preflight is an observation, not a lock against a control-plane restore.
No grants, role changes, production writes, or credential fallback are made.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
from urllib.parse import parse_qsl, unquote, urlsplit

HOST = 'ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech'
IDENTITY = {
    'neon.project_id': ('misty-poetry-18012774', 'postmaster'),
    'neon.branch_id': ('br-aged-mud-b1i64914', 'postmaster'),
    'neon.endpoint_id': ('ep-noisy-pine-b1pe30sf', 'superuser'),
}
OPTIONS = ('-c default_transaction_read_only=on -c statement_timeout=600000 '
           '-c lock_timeout=5000 -c row_security=off -c search_path=pg_catalog')

PREFLIGHT = """
BEGIN READ ONLY;
SET LOCAL statement_timeout='10s';
SELECT json_build_object(
 'session', json_build_array(current_database(),current_user,session_user,
                            current_setting('transaction_read_only')),
 'version', current_setting('server_version_num')::int / 10000,
 'identity', (SELECT json_agg(json_build_array(name,setting,context,source,reset_val,pending_restart))
              FROM pg_settings WHERE name IN ('neon.project_id','neon.branch_id','neon.endpoint_id')),
 'denied_schemas', (SELECT count(*) FROM pg_namespace
   WHERE nspname !~ '^pg_' AND nspname <> 'information_schema'
     AND NOT has_schema_privilege(oid,'USAGE')),
 'denied_tables', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','m')
     AND (NOT has_table_privilege(c.oid,'SELECT') OR row_security_active(c.oid))),
 'denied_sequences', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind='S'
     AND NOT has_sequence_privilege(c.oid,'SELECT')),
 'denied_largeobjects', (SELECT count(*) FROM pg_largeobject_metadata
     WHERE NOT has_largeobject_privilege(oid,'SELECT'))
);
ROLLBACK;
"""
STATS = """SELECT
 (SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema'))::text || '|' ||
 (SELECT count(*) FROM information_schema.schemata WHERE schema_name NOT IN ('pg_catalog','information_schema'))::text || '|' ||
 (SELECT count(*) FROM assistant_lab.job)::text || '|' ||
 (SELECT count(*) FROM assistant_lab.research_job)::text;
"""


def require(value):
    if not value:
        raise ValueError('BACKUP_SOURCE_REFUSED')


def parameters(raw):
    """Reject overrides, then rebuild fixed direct TLS settings for ALL clients."""
    require(isinstance(raw, str) and 0 < len(raw) <= 8192)
    require(not any(k.startswith('PG') for k in os.environ))
    value = raw.strip()
    require(not any(ord(c) < 32 or ord(c) == 127 for c in value))
    uri = urlsplit(value)
    pairs = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
    query = dict(pairs)
    require(len(query) == len(pairs))
    require(set(query) <= {'sslmode', 'channel_binding', 'connect_timeout', 'application_name'})
    require(uri.scheme in ('postgres', 'postgresql') and not uri.fragment
            and uri.hostname in (HOST, HOST.replace('.c-5.', '-pooler.c-5.'))
            and uri.port in (None, 5432) and uri.path == '/neondb'
            and unquote(uri.username or '') == 'neondb_owner'
            and query.get('sslmode') in ('require', 'verify-full')
            and query.get('channel_binding') == 'require')
    password = unquote(uri.password or '')
    require(password and not any(ord(c) < 32 or ord(c) == 127 for c in password))
    return dict(PGHOST=HOST, PGPORT='5432', PGDATABASE='neondb', PGUSER='neondb_owner',
                PGPASSWORD=password, PGSSLMODE='verify-full',
                PGSSLROOTCERT='/backup-ca.crt',
                PGCHANNELBINDING='require', PGGSSENCMODE='disable', PGCONNECT_TIMEOUT='10',
                PGAPPNAME='neon-independent-backup', PGOPTIONS=OPTIONS)


def client(pg, args, *, sql=None, dump=False):
    name = 'neon-backup-' + uuid.uuid4().hex
    command = ['docker', 'run', '--rm', '--name', name, '-i',
               '--mount', 'type=bind,source=/etc/ssl/certs/ca-certificates.crt,target=/backup-ca.crt,readonly']
    for key in pg:
        command.extend(['-e', key])  # Values never enter argv or output.
    if dump:
        command.extend(['-v', str(Path.cwd()) + ':/backup'])
    command.extend(['postgres:18', *args])
    # Do not pass the original URI, passphrase or ambient libpq settings to Docker.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('PG') and k not in ('DATABASE_URL', 'BACKUP_PASSPHRASE')}
    try:
        result = subprocess.run(command, input=sql, text=True, capture_output=True,
                                env={**env, **pg}, timeout=660 if dump else 45)
    except subprocess.TimeoutExpired:
        # Killing the Docker CLI alone does not stop the daemon's container.
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, env=env, timeout=15)
        raise ValueError('BACKUP_SOURCE_REFUSED') from None
    require(result.returncode == 0)
    return result.stdout.strip()


def preflight(pg):
    row = json.loads(client(pg, ['psql', '-XqAt', '-v', 'ON_ERROR_STOP=1'], sql=PREFLIGHT))
    require(row.get('session') == ['neondb', 'neondb_owner', 'neondb_owner', 'on'])
    require(row.get('version') == 18)
    tags = row.get('identity')
    require(isinstance(tags, list) and len(tags) == 3)
    require({r[0] for r in tags} == set(IDENTITY))
    for name, setting, context, source, reset, pending in tags:
        require((setting, context) == IDENTITY[name] and source == 'configuration file'
                and reset == setting and pending is False)
    for key in ('denied_schemas', 'denied_tables', 'denied_sequences', 'denied_largeobjects'):
        require(type(row.get(key)) is int and row[key] == 0)


def main(mode):
    require(mode in ('preflight', 'stats', 'dump'))
    pg = parameters(os.environ.pop('DATABASE_URL', ''))
    preflight(pg)
    if mode == 'stats':
        stats = client(pg, ['psql', '-XqAt', '-v', 'ON_ERROR_STOP=1'], sql=STATS)
        require(len(stats.split('|')) == 4 and all(x.isascii() and x.isdigit() for x in stats.split('|')))
        print(stats)
    elif mode == 'dump':
        client(pg, ['pg_dump', '--format=custom', '--compress=9', '--no-owner', '--lock-wait-timeout=5s',
                    '--no-privileges', '--file=/backup/neon.dump'], dump=True)
    else:
        print('BACKUP_SOURCE_PREFLIGHT_PASS')


def entrypoint():
    try:
        require(len(sys.argv) == 2)
        main(sys.argv[1])
    except BaseException:
        # libpq/subprocess/URI exceptions may contain private connection material.
        print('BACKUP_SOURCE_REFUSED', file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == '__main__':
    entrypoint()
