"""Six-grant transaction engine; no production CLI or maintenance implementation.

The caller must supply independently approved metadata and real external writer
exclusion. Table locks and the guard interface cannot themselves establish that
exclusion. A durable before/expected-after record precedes any GRANT, allowing
reconciliation after loss of the commit response without blindly retrying.
"""
import hashlib
import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

from psycopg import sql

FUNCTIONS = (
    'autopilot.native_cli_reserve(uuid,jsonb,text)',
    'autopilot.native_cli_snapshot(uuid)',
    'autopilot.native_cli_current(jsonb)',
    'autopilot.native_cli_begin(jsonb)',
    'autopilot.native_cli_ack(jsonb,text,text)',
    'autopilot.native_cli_finish(jsonb,text,jsonb)',
)
HELPER = 'autopilot.native_cli_authority_locked(uuid,jsonb)'
LOCK_TABLES = ('native_cli_config', 'native_cli_receipt', 'project_work_item',
               'project_work_task', 'role_dispatch_outbox', 'step_attempt', 'task')


class Refused(RuntimeError):
    pass


def check(condition, code):
    if not condition:
        raise Refused(code)


@dataclass(frozen=True)
class NeonBinding:
    project_id: str
    branch_id: str
    endpoint_id: str
    host: str


@dataclass(frozen=True)
class Target:
    database: str
    session_owner: str
    owner: str
    recipient: str
    neon: NeonBinding | None = None


class MaintenanceGuard:
    def assert_held(self, target, operation):
        raise Refused('MAINTENANCE_IMPLEMENTATION_REQUIRED')


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def save_manifest(path, record):
    """Create a private, non-overwriting record; caller must retain returned hash."""
    path = Path(path)
    parent = path.parent.stat()
    check(parent.st_uid == os.getuid() and stat.S_IMODE(parent.st_mode) & 0o077 == 0,
          'PRIVATE_MANIFEST_DIRECTORY_REQUIRED')
    data = encode(record)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    return hashlib.sha256(data).hexdigest()


def load_manifest(path, expected_digest):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        check(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
              and stat.S_IMODE(info.st_mode) & 0o077 == 0, 'PRIVATE_MANIFEST_REQUIRED')
        data = handle.read(4 * 1024 * 1024 + 1)
    check(len(data) <= 4 * 1024 * 1024, 'MANIFEST_TOO_LARGE')
    check(hashlib.sha256(data).hexdigest() == expected_digest, 'MANIFEST_DIGEST_MISMATCH')
    return json.loads(data)


def identity(conn, target):
    check(conn.execute('SELECT current_database(),session_user,current_user').fetchone()
          == (target.database, target.session_owner, target.owner), 'TARGET_IDENTITY_MISMATCH')
    if target.neon is None:
        # The only unbound target is the existing disposable regression fixture.
        params = conn.info.get_parameters()
        check((target.database, target.session_owner, target.owner, target.recipient)
              == ('bridge_school_ci', 'postgres', 'bridge_ci_owner', 'native_commit_login')
              and conn.info.host == 'localhost' and conn.info.port == 5432
              and conn.info.hostaddr in ('127.0.0.1', '::1')
              and params.get('host') == 'localhost'
              and params.get('hostaddr', conn.info.hostaddr) == conn.info.hostaddr
              and not params.get('options'),
              'NEON_BINDING_REQUIRED')
        return
    neon_identity(conn, target.neon)


def neon_identity(conn, binding):
    """Verify actual routing and server tags, never infer branch from DB name."""
    params = conn.info.get_parameters()
    check(binding.host.startswith(binding.endpoint_id + '.')
          and binding.host.endswith('.aws.neon.tech')
          and binding.endpoint_id.startswith('ep-')
          and '-pooler.' not in binding.host, 'NEON_DIRECT_HOST_REQUIRED')
    check(conn.info.host == binding.host and conn.info.port == 5432
          and params.get('host') == binding.host, 'NEON_CONNECTION_HOST_MISMATCH')
    # GSS can take precedence over SSL in libpq. Disabling it makes verify-full
    # a requirement for this actual connection, not merely a fallback option.
    check(params.get('sslmode') == 'verify-full'
          and params.get('gssencmode') == 'disable', 'NEON_VERIFIED_TLS_REQUIRED')
    # Psycopg 3 resolves DNS into hostaddr itself. Post-connect metadata cannot
    # distinguish that address from caller input: authenticate the hostname via
    # verify-full and check branch tags instead of claiming input provenance.
    check(not params.get('options')
          and params.get('hostaddr', conn.info.hostaddr) == conn.info.hostaddr,
          'NEON_ROUTING_OVERRIDE_REFUSED')
    neon_server_identity(conn, binding)


def neon_server_identity(conn, binding):
    expected = {
        'neon.project_id': (binding.project_id, 'postmaster'),
        'neon.branch_id': (binding.branch_id, 'postmaster'),
        'neon.endpoint_id': (binding.endpoint_id, 'superuser'),
    }
    rows = conn.execute("""SELECT name, setting, context, source, reset_val, pending_restart
      FROM pg_catalog.pg_settings WHERE name = ANY(%s)""", (list(expected),)).fetchall()
    check(len(rows) == len(expected), 'NEON_SERVER_IDENTITY_MISSING')
    for name, setting, context, source, reset_val, pending_restart in rows:
        check((setting, context) == expected[name] and reset_val == setting
              and source == 'configuration file' and not pending_restart,
              'NEON_SERVER_IDENTITY_MISMATCH')


def snapshot(conn, target):
    identity(conn, target)
    state = conn.execute("""
      SELECT jsonb_build_object(
        'functions',(SELECT jsonb_agg(jsonb_build_object(
          'oid',p.oid::bigint,'name',p.proname,'owner',p.proowner::bigint,
          'definition_sha256',encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex'),
          'acl',(SELECT jsonb_agg(jsonb_build_object('grantor',a.grantor::bigint,
            'grantee',a.grantee::bigint,'privilege_type',a.privilege_type,'is_grantable',a.is_grantable)
            ORDER BY a.grantee,a.grantor,a.privilege_type,a.is_grantable)
            FROM aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a)) ORDER BY p.oid)
          FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
          WHERE n.nspname='autopilot' AND p.prokind='f'),
        'tables',(SELECT jsonb_agg(jsonb_build_object('oid',c.oid::bigint,'name',c.relname,
          'owner',c.relowner::bigint,'acl',c.relacl::text,
          'columns',(SELECT jsonb_agg(jsonb_build_object('number',a.attnum,'acl',a.attacl::text)
            ORDER BY a.attnum) FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0
            AND NOT a.attisdropped)) ORDER BY c.oid)
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='autopilot'),
        'triggers',(SELECT jsonb_agg(jsonb_build_object('oid',t.oid::bigint,'enabled',t.tgenabled,
          'definition',pg_get_triggerdef(t.oid,false)) ORDER BY t.oid)
          FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='autopilot' AND NOT t.tgisinternal),
        'schema',(SELECT jsonb_build_object('owner',nspowner::bigint,'acl',nspacl::text)
          FROM pg_namespace WHERE nspname='autopilot'),
        'roles',(SELECT jsonb_agg(jsonb_build_object('oid',oid::bigint,'name',rolname,
          'superuser',rolsuper,'inherit',rolinherit,'create_role',rolcreaterole,'create_db',rolcreatedb,
          'login',rolcanlogin,'replication',rolreplication,'bypass_rls',rolbypassrls,
          'config',rolconfig,'connection_limit',rolconnlimit) ORDER BY oid) FROM pg_roles),
        'membership',(SELECT jsonb_agg(to_jsonb(m) ORDER BY roleid,member,grantor) FROM pg_auth_members m),
        'config',(SELECT jsonb_agg(to_jsonb(c)) FROM autopilot.native_cli_config c),
        'receipts',(SELECT count(*) FROM autopilot.native_cli_receipt),
        'nonterminal_tasks',(SELECT count(*) FROM autopilot.task WHERE status NOT IN
          ('OWNER_REQUIRED','DONE','FAILED_CLOSED','BUDGET_STOP','CANCELLED')))
    """).fetchone()[0]
    state['target'] = asdict(target)
    state['function_ids'] = {sig: conn.execute('SELECT %s::regprocedure::oid', (sig,)).fetchone()[0]
                             for sig in (*FUNCTIONS, HELPER)}
    state['recipient_oid'] = conn.execute('SELECT %s::regrole::oid', (target.recipient,)).fetchone()[0]
    state['owner_oid'] = conn.execute('SELECT %s::regrole::oid', (target.owner,)).fetchone()[0]
    return state


def dormant(state):
    check(len(state['config'] or []) == 1 and state['config'][0]['enabled'] is False,
          'CONFIG_NOT_DISABLED')
    check(state['receipts'] == 0, 'RECEIPTS_PRESENT')
    check(state['nonterminal_tasks'] == 0, 'QUEUE_NOT_EMPTY')


def privileges(conn, target, enabled):
    for sig in FUNCTIONS:
        check(conn.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                           (target.recipient, sig)).fetchone()[0] is enabled, 'RPC_PRIVILEGE_MISMATCH')
    check(not conn.execute("SELECT has_function_privilege(%s,%s,'EXECUTE')",
                           (target.recipient, HELPER)).fetchone()[0], 'HELPER_ACCESS')
    for table in ('autopilot.native_cli_config', 'autopilot.native_cli_receipt'):
        check(not conn.execute("""SELECT
          has_table_privilege(%s,%s,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
          OR has_any_column_privilege(%s,%s,'SELECT,INSERT,UPDATE,REFERENCES')""",
          (target.recipient, table, target.recipient, table)).fetchone()[0], 'NATIVE_TABLE_ACCESS')
    check(conn.execute("SELECT has_schema_privilege(%s,'autopilot','USAGE')",
                       (target.recipient,)).fetchone()[0], 'SCHEMA_USAGE_REQUIRED')


def expected_after(before):
    after = json.loads(json.dumps(before))
    target_ids = {before['function_ids'][sig] for sig in FUNCTIONS}
    native_ids = {f['oid'] for f in before['functions'] if f['name'].startswith('native_cli_')}
    check(native_ids == target_ids | {before['function_ids'][HELPER]}, 'NATIVE_FUNCTION_SET_DRIFT')
    for function in after['functions']:
        if function['oid'] not in target_ids:
            continue
        check(function['owner'] == before['owner_oid'], 'FUNCTION_OWNER_MISMATCH')
        entry = dict(grantor=function['owner'], grantee=before['recipient_oid'],
                     privilege_type='EXECUTE', is_grantable=False)
        check(not any(a['grantee'] == before['recipient_oid'] for a in function['acl']),
              'PREEXISTING_DIRECT_ACL')
        function['acl'].append(entry)
        function['acl'].sort(key=lambda a: (a['grantee'], a['grantor'], a['privilege_type'], a['is_grantable']))
    return after


def prepare(conn, target, approved_snapshot_digest, path):
    """Read-only preparation; reference digest must come from independent review."""
    check(conn.autocommit, 'AUTOCOMMIT_CONNECTION_REQUIRED')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='5s'")
        before = snapshot(conn, target)
        dormant(before)
        privileges(conn, target, False)
        check(digest(before) == approved_snapshot_digest, 'APPROVED_BASELINE_MISMATCH')
        record = dict(version=1, target=asdict(target), before=before, after=expected_after(before))
    return save_manifest(path, record)


def inspect(conn, target, path, manifest_digest):
    check(conn.autocommit, 'AUTOCOMMIT_CONNECTION_REQUIRED')
    record = load_manifest(path, manifest_digest)
    check(record['version'] == 1 and record['target'] == asdict(target), 'MANIFEST_TARGET_MISMATCH')
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        conn.execute("SET LOCAL statement_timeout='5s'")
        actual = snapshot(conn, target)
    return 'BEFORE' if actual == record['before'] else 'AFTER' if actual == record['after'] else 'DRIFT'


def change(conn, target, path, manifest_digest, guard, *, rollback=False, database_fence=None):
    check(conn.autocommit, 'AUTOCOMMIT_CONNECTION_REQUIRED')
    record = load_manifest(path, manifest_digest)
    check(record['version'] == 1 and record['target'] == asdict(target), 'MANIFEST_TARGET_MISMATCH')
    check(record['after'] == expected_after(record['before']), 'MANIFEST_DELTA_INVALID')
    operation = 'rollback' if rollback else 'apply'
    guard.assert_held(target, operation)
    with conn.transaction():
        conn.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        identity(conn, target)
        # Serialize cooperative engine calls even when SHARE locks are compatible.
        conn.execute('SELECT pg_advisory_xact_lock(1971, 6)')
        if database_fence is not None:
            database_fence.protect_transaction(conn, target)
        else:
            for table in LOCK_TABLES:
                conn.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE')
                             .format(sql.Identifier('autopilot', table)))
        actual = snapshot(conn, target)
        dormant(actual)
        check(actual == record['after' if rollback else 'before'], 'ROLLBACK_DRIFT' if rollback else 'BASELINE_DRIFT')
        privileges(conn, target, rollback)
        guard.assert_held(target, operation)
        for sig in FUNCTIONS:
            command = ('REVOKE EXECUTE ON FUNCTION {} FROM {} RESTRICT' if rollback
                       else 'GRANT EXECUTE ON FUNCTION {} TO {}')
            conn.execute(sql.SQL(command).format(sql.SQL(sig), sql.Identifier(target.recipient)))
        privileges(conn, target, not rollback)
        check(snapshot(conn, target) == record['before' if rollback else 'after'], 'POSTCHECK_MISMATCH')
        guard.assert_held(target, operation)
        if database_fence is not None:
            database_fence.assert_held(target)
    if database_fence is not None:
        try:
            database_fence.assert_held(target)
        except Exception as exc:
            raise Refused('COMMITTED_BUT_WRITE_FENCE_POSTCHECK_FAILED') from exc
    # A lost COMMIT response leaves the durable record intact. Call inspect()
    # from a NEW connection; never infer rollback from a client exception.
    return 'BEFORE' if rollback else 'AFTER'
