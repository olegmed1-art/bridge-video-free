"""Backup-only production route. No credentials or client errors reach logs.

The preflight is an observation, not a lock against a control-plane restore.
No grants, role changes, production writes, or credential fallback are made.
"""
import json
import os
import re
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
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
     AND CASE WHEN c.relkind IN ('r','p','m') THEN NOT has_table_privilege(c.oid,'SELECT') ELSE false END),
 'denied_rls', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
     AND CASE WHEN c.relkind IN ('r','p','m') THEN row_security_active(c.oid) ELSE false END),
 'denied_sequences', (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema'
     AND CASE WHEN c.relkind='S' THEN NOT has_sequence_privilege(c.oid,'SELECT') ELSE false END),
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


FAILURE_CODES = frozenset({
    'CONTRACT_REFUSED', 'AUTHENTICATION_FAILED', 'TLS_FAILED', 'NETWORK_FAILED',
    'CONNECT_TIMEOUT', 'STATEMENT_TIMEOUT', 'LOCK_TIMEOUT', 'CLIENT_TIMEOUT',
    'CLIENT_TIMEOUT_CLEANUP_FAILED', 'TOOL_FAILED', 'SQL_FAILED', 'ACL_DENIED',
    'RLS_ACTIVE', 'IDENTITY_MISMATCH', 'READ_ONLY_REFUSED', 'OUTPUT_INVALID',
    'URI_POLICY_REFUSED', 'CONTEXT_REFUSED', 'SOURCE_DRIFT', 'SOURCE_CHECK_FAILED',
})


class BackupFailure(ValueError):
    def __init__(self, code):
        self.code = code if code in FAILURE_CODES else 'TOOL_FAILED'
        super().__init__(self.code)


def require(value, code='CONTRACT_REFUSED'):
    if not value:
        raise BackupFailure(code)


def classify_client_failure(returncode, stderr):
    """Inspect captured English diagnostics in memory; return ONLY a fixed code.

    Codes describe the client-reported failure, not a proven root cause. Never
    emit matched substrings, SQLSTATEs, stdout, stderr or exception messages.
    """
    if returncode in (125, 126, 127) or returncode < 0:
        return 'TOOL_FAILED'
    message = stderr.lower() if isinstance(stderr, str) else ''
    # psql VERBOSITY=verbose enables structured SQLSTATE prefixes for SQL errors.
    diagnostics = re.findall(r'(?:error|fatal):\s+([0-9a-z]{5}):\s*([^\r\n]*)', message)
    if diagnostics:
        state, primary = diagnostics[0]
        if state in ('28p01', '28000'):
            return 'AUTHENTICATION_FAILED'
        if state == '57014' and primary.startswith('canceling statement due to statement timeout'):
            return 'STATEMENT_TIMEOUT'
        if state == '55p03' and primary.startswith('canceling statement due to lock timeout'):
            return 'LOCK_TIMEOUT'
        if state == '42501':
            return ('RLS_ACTIVE' if primary.startswith(('query would be affected by row-level security',
                    'new row violates row-level security')) else 'ACL_DENIED')
        if state == '25006':
            return 'READ_ONLY_REFUSED'
        return 'SQL_FAILED'
    if any(x in message for x in
            ('password authentication failed', 'no password supplied', 'authentication failed')):
        return 'AUTHENTICATION_FAILED'
    if any(x in message for x in ('certificate verify failed', 'root certificate file',
           'server certificate', 'ssl error', 'tls error', 'does not support ssl',
           'channel binding required', 'channel binding is required')):
        return 'TLS_FAILED'
    if 'canceling statement due to statement timeout' in message:
        return 'STATEMENT_TIMEOUT'
    if 'canceling statement due to lock timeout' in message:
        return 'LOCK_TIMEOUT'
    if 'timeout expired' in message or 'connection timed out' in message:
        return 'CONNECT_TIMEOUT'
    if any(x in message for x in ('could not translate host name', 'connection refused',
           'network is unreachable', 'no route to host', 'could not resolve hostname',
           'server closed the connection unexpectedly', 'connection reset by peer')):
        return 'NETWORK_FAILED'
    if 'row-level security' in message or 'row level security' in message:
        return 'RLS_ACTIVE'
    if 'permission denied for' in message:
        return 'ACL_DENIED'
    if returncode == 3:  # psql ON_ERROR_STOP script failure
        return 'SQL_FAILED'
    return 'TOOL_FAILED'


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
    command = ['docker', 'run', '--rm', '--name', name, '-i', '-e', 'LC_ALL=C',
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
        try:
            cleanup = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, env=env, timeout=15)
            require(cleanup.returncode == 0, 'CLIENT_TIMEOUT_CLEANUP_FAILED')
        except (OSError, subprocess.TimeoutExpired):
            raise BackupFailure('CLIENT_TIMEOUT_CLEANUP_FAILED') from None
        raise BackupFailure('CLIENT_TIMEOUT') from None
    except (OSError, UnicodeError):
        raise BackupFailure('TOOL_FAILED') from None
    if result.returncode != 0:
        raise BackupFailure(classify_client_failure(result.returncode, result.stderr))
    return result.stdout.strip()


def preflight(pg, *, gates=None):
    payload = client(pg, ['psql', '-XqAt', '-v', 'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose'], sql=PREFLIGHT)
    try:
        row = json.loads(payload)
    except (ValueError, TypeError):
        raise BackupFailure('OUTPUT_INVALID') from None
    require(type(row) is dict, 'OUTPUT_INVALID')
    session = row.get('session')
    require(type(session) is list and len(session) == 4, 'OUTPUT_INVALID')
    require(session[:3] == ['neondb', 'neondb_owner', 'neondb_owner'], 'IDENTITY_MISMATCH')
    if gates is not None:
        gates['auth'] = 'PASS'
    require(session[3] == 'on', 'READ_ONLY_REFUSED')
    if gates is not None:
        gates['readonly'] = 'PASS'
    require(type(row.get('version')) is int and row['version'] == 18, 'IDENTITY_MISMATCH')
    tags = row.get('identity')
    require(type(tags) is list and len(tags) == 3, 'IDENTITY_MISMATCH')
    require(all(type(r) is list and len(r) == 6 and type(r[0]) is str for r in tags), 'OUTPUT_INVALID')
    require({r[0] for r in tags} == set(IDENTITY), 'IDENTITY_MISMATCH')
    for name, setting, context, source, reset, pending in tags:
        require((setting, context) == IDENTITY[name] and source == 'configuration file'
                and reset == setting and pending is False, 'IDENTITY_MISMATCH')
    if gates is not None:
        gates['identity'] = 'PASS'
    for key in ('denied_schemas', 'denied_tables', 'denied_sequences', 'denied_largeobjects'):
        require(type(row.get(key)) is int and row[key] >= 0, 'OUTPUT_INVALID')
        require(row[key] == 0, 'ACL_DENIED')
    if gates is not None:
        gates['acl'] = 'PASS'
    require(type(row.get('denied_rls')) is int and row['denied_rls'] >= 0, 'OUTPUT_INVALID')
    require(row['denied_rls'] == 0, 'RLS_ACTIVE')
    if gates is not None:
        gates['rls'] = 'PASS'


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
    except BaseException as exc:
        # libpq/subprocess/URI exceptions may contain private connection material.
        code = exc.code if isinstance(exc, BackupFailure) and exc.code in FAILURE_CODES else 'TOOL_FAILED'
        print('BACKUP_SOURCE_REFUSED:' + code, file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == '__main__':
    entrypoint()
