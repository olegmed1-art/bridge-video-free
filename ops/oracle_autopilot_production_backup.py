"""Production PostgreSQL logical backup with an off-host independent restore drill.

Run on the trusted backup host with libpq PG* credentials, OCI SDK credentials,
and a private pre-existing Object Storage bucket. No production writes occur.
The JSON receipt is evidence only; the cutover gate must independently validate
the object's identity and the trusted workflow run before accepting it.
"""
import hashlib
import json
import os
import fcntl
from contextlib import contextmanager
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from ops.oracle_light_candidate_restore import MANIFEST, ROLES

FORMAT = 'PRODUCTION_AUTOPILOT_BACKUP_V1'
IDENT = re.compile(r'^[a-z][a-z0-9_]{0,62}$')
OBJECT = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,255}$')
MAX_BYTES = 8 * 1024**3
ROUTE_ROOT = Path('/var/lib/bridge-autopilot-tunnel')
OWNERSHIP_SQL = """SELECT coalesce(jsonb_agg(jsonb_build_array(n.nspname,p.proname,
 pg_get_function_identity_arguments(p.oid),r.rolname,p.prosecdef)
 ORDER BY n.nspname,p.proname,pg_get_function_identity_arguments(p.oid)),'[]'::jsonb)
 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 JOIN pg_roles r ON r.oid=p.proowner
 WHERE n.nspname IN ('autopilot','autopilot_reconcile')"""


def required(name):
    value = os.environ.get(name, '')
    if not value:
        raise ValueError(name + '_REQUIRED')
    return value


def execute(argv, *, env=None, timeout=900, stdin=None):
    return subprocess.run(argv, env=env, input=stdin, check=True,
                          capture_output=True, timeout=timeout).stdout


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def libpq_environment(dsn):
    from psycopg.conninfo import conninfo_to_dict
    options = conninfo_to_dict(dsn)
    allowed = {'host': 'PGHOST', 'hostaddr': 'PGHOSTADDR', 'port': 'PGPORT',
               'user': 'PGUSER', 'password': 'PGPASSWORD', 'dbname': 'PGDATABASE',
               'sslmode': 'PGSSLMODE', 'sslrootcert': 'PGSSLROOTCERT',
               'sslcert': 'PGSSLCERT', 'sslkey': 'PGSSLKEY',
               'connect_timeout': 'PGCONNECT_TIMEOUT', 'target_session_attrs': 'PGTARGETSESSIONATTRS'}
    if not options or 'hostaddr' in options or set(options) - set(allowed):
        raise ValueError('UNSUPPORTED_LIBPQ_PARAMETERS')
    env = {name: os.environ[name] for name in ('PATH', 'LANG', 'LC_ALL') if name in os.environ}
    for key, value in options.items():
        env[allowed[key]] = str(value)
    if env.get('PGDATABASE') != 'autopilot' or env.get('PGHOST') != '127.0.0.1' or env.get('PGPORT') != '55432':
        raise ValueError('DATABASE_IDENTITY_REQUIRED')
    if any(os.environ.get(name) for name in ('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE')):
        raise ValueError('LIBPQ_HOST_OVERRIDE_FORBIDDEN')
    return env


@contextmanager
def pinned_route():
    if os.geteuid() != 0 or socket.gethostname() != 'autopilot-lite-vnic':
        raise ValueError('LIGHT_ORACLE_HOST_REQUIRED')
    for path in (ROUTE_ROOT, ROUTE_ROOT / 'route.json', ROUTE_ROOT / 'route.lock',
                 ROUTE_ROOT / 'lock-identity.json'):
        st = path.lstat()
        if path.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
            raise ValueError('ROUTE_FILE_UNTRUSTED')
    lock_path = ROUTE_ROOT / 'route.lock'
    lock_stat = lock_path.lstat()
    fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as lock:
        st = os.fstat(lock.fileno())
        if not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (lock_stat.st_dev, lock_stat.st_ino):
            raise ValueError('ROUTE_LOCK_SWAPPED')
        identity = json.loads((ROUTE_ROOT / 'lock-identity.json').read_text())
        if identity != {'device': st.st_dev, 'inode': st.st_ino}:
            raise ValueError('ROUTE_LOCK_IDENTITY_DRIFT')
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        try:
            route = read_route_file()
            if (set(route) != {'version', 'backend', 'database', 'epoch'} or
                route['version'] != 1 or route['backend'] != 'postgresql' or
                route['database'] != 'autopilot' or type(route['epoch']) is not int or
                route['epoch'] < 1):
                raise ValueError('PRODUCTION_ROUTE_NOT_ACTIVE')
            yield route
            if read_route_file() != route:
                raise ValueError('ROUTE_CHANGED_DURING_BACKUP')
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_route_file():
    path = ROUTE_ROOT / 'route.json'
    before = path.lstat()
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC), 'rb') as stream:
        current = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino) or \
           not stat.S_ISREG(current.st_mode) or current.st_uid != 0 or current.st_mode & 0o022:
            raise ValueError('ROUTE_FILE_SWAPPED')
        raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('ROUTE_FILE_OVERSIZE')
        return json.loads(raw)


def source_snapshot(dsn):
    import psycopg
    connection = psycopg.connect(dsn, connect_timeout=10,
                                 application_name='oracle-autopilot-production-backup')
    # psycopg otherwise starts an implicit transaction before explicit BEGIN,
    # silently leaving the export in READ COMMITTED.
    connection.autocommit = True
    cursor = connection.cursor()
    # MANIFEST creates a temporary table. The database account must have only
    # SELECT and TEMP privileges; this transaction never issues permanent DML.
    cursor.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ')
    cursor.execute('SELECT pg_export_snapshot(), current_database(), '
                   "current_setting('server_version_num')::int, "
                   "(SELECT oid::text FROM pg_database WHERE datname=current_database()), "
                   'clock_timestamp(), inet_server_addr()::text, inet_server_port()')
    snapshot, database, version, db_oid, observed, address, port = cursor.fetchone()
    if version // 10000 != 18 or database != 'autopilot' or address != '127.0.0.1' or port != 55432:
        connection.close()
        raise ValueError('SOURCE_IDENTITY_MISMATCH')
    # pg_dump exports the entire database. Refuse unexpected application data,
    # including a shadow schema accidentally restored into this database.
    cursor.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                   "WHERE c.relkind IN ('r','p','v','m','S','f') "
                   "AND n.nspname NOT LIKE 'pg\\_%' ESCAPE '\\' "
                   "AND n.nspname <> 'information_schema' "
                   "AND NOT EXISTS (SELECT 1 FROM pg_depend d "
                   "WHERE d.classid='pg_class'::regclass AND d.objid=c.oid AND d.deptype='e') "
                   "AND NOT (n.nspname IN ('autopilot','autopilot_reconcile') "
                   "OR (n.nspname='public' AND c.relname IN "
                   "('schema_migration','autopilot_operational_health_signal')))")
    if cursor.fetchone()[0]:
        connection.close()
        raise ValueError('UNEXPECTED_DATABASE_RELATIONS')
    cursor.execute('SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)', (list(ROLES),))
    existing_roles = {row[0] for row in cursor.fetchall()}
    if not {'autopilot_callback_login', 'autopilot_light_worker_login',
            'bridge_school_worker_principal'} <= existing_roles:
        connection.close()
        raise ValueError('SOURCE_ROLES_MISSING')
    cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                   "FROM pg_roles WHERE rolname=current_user")
    if any(cursor.fetchone()):
        connection.close()
        raise ValueError('BACKUP_ACCOUNT_OVERPRIVILEGED')
    cursor.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                   "WHERE n.nspname IN ('autopilot','autopilot_reconcile','public') "
                   "AND c.relkind IN ('r','p','m') AND "
                   "(has_table_privilege(current_user,c.oid,'INSERT') OR "
                   "has_table_privilege(current_user,c.oid,'UPDATE') OR "
                   "has_table_privilege(current_user,c.oid,'DELETE') OR "
                   "has_table_privilege(current_user,c.oid,'TRUNCATE'))")
    if cursor.fetchone()[0]:
        connection.close()
        raise ValueError('BACKUP_ACCOUNT_CAN_WRITE')
    cursor.execute(MANIFEST)
    while cursor.nextset():
        pass
    manifest = cursor.fetchone()[0]
    if not isinstance(manifest, dict) or len(manifest) < 1:
        connection.close()
        raise ValueError('EMPTY_MANIFEST')
    cursor.execute(OWNERSHIP_SQL)
    owners = cursor.fetchone()[0]
    if not owners or any(item[3] not in existing_roles for item in owners):
        connection.close()
        raise ValueError('UNSUPPORTED_FUNCTION_OWNER')
    cursor.execute("SELECT role.rolname, member.rolname FROM pg_auth_members a "
                   "JOIN pg_roles role ON role.oid=a.roleid "
                   "JOIN pg_roles member ON member.oid=a.member "
                   "WHERE role.rolname = ANY(%s) AND member.rolname = ANY(%s) "
                   "ORDER BY role.rolname,member.rolname", (list(ROLES), list(ROLES)))
    memberships = cursor.fetchall()
    return connection, snapshot, database, db_oid, observed, manifest, owners, existing_roles, memberships


def private_bucket(client, namespace, bucket):
    detail = client.get_bucket(namespace, bucket).data
    if detail.public_access_type != 'NoPublicAccess' or detail.storage_tier != 'Standard':
        raise ValueError('UNSAFE_BUCKET')
    if detail.compartment_id != required('OCI_TENANCY'):
        raise ValueError('BUCKET_COMPARTMENT_MISMATCH')
    import oci
    links = oci.pagination.list_call_get_all_results(
        client.list_preauthenticated_requests, namespace, bucket).data
    if links:
        raise ValueError('PREAUTHENTICATED_BUCKET_LINKS')
    return detail


def upload_download(source, destination, name):
    import oci
    config = oci.config.from_file(required('OCI_CONFIG_FILE'), required('OCI_PROFILE'))
    if config['tenancy'] != required('OCI_TENANCY'):
        raise ValueError('TENANCY_MISMATCH')
    client = oci.object_storage.ObjectStorageClient(config,
             retry_strategy=oci.retry.NoneRetryStrategy(), timeout=(10, 120))
    namespace = client.get_namespace(compartment_id=config['tenancy']).data
    bucket = required('AUTOPILOT_BACKUP_BUCKET')
    private_bucket(client, namespace, bucket)
    length = source.stat().st_size
    if not 0 < length <= MAX_BYTES:
        raise ValueError('BACKUP_SIZE_BOUND')
    sha = digest(source)
    with source.open('rb') as stream:
        client.put_object(namespace, bucket, name, stream, content_length=length,
                          if_none_match='*', content_type='application/octet-stream',
                          opc_meta={'sha256': sha, 'format': FORMAT})
    private_bucket(client, namespace, bucket)
    response = client.get_object(namespace, bucket, name)
    if int(response.headers['content-length']) != length or response.headers.get('opc-meta-sha256') != sha:
        raise ValueError('OBJECT_METADATA_MISMATCH')
    received = hashlib.sha256()
    count = 0
    with destination.open('xb') as output:
        os.chmod(destination, 0o600)
        for block in response.data.raw.stream(1024 * 1024, decode_content=False):
            count += len(block)
            if count > length:
                raise ValueError('OBJECT_OVERFLOW')
            output.write(block)
            received.update(block)
    if count != length or received.hexdigest() != sha:
        raise ValueError('DOWNLOAD_MISMATCH')
    return namespace, bucket, sha


def drill(archive, expected, owners, roles, memberships):
    # An ephemeral PG18 container, isolated from the host network and production
    # volume. The archive is mounted read-only; --rm removes the drill database.
    image = required('AUTOPILOT_RESTORE_IMAGE')
    if not image.startswith('postgres@sha256:'):
        raise ValueError('RESTORE_IMAGE_MUST_BE_PINNED')
    if os.geteuid() != 0:
        raise ValueError('ROOT_REQUIRED_FOR_PRIVATE_DRILL_MOUNTS')
    # Container UID 999 can read but cannot alter a root-owned archive.
    os.chown(archive.parent, 0, 999)
    os.chmod(archive.parent, 0o710)
    os.chown(archive, 0, 999)
    os.chmod(archive, 0o640)
    command = ('set -eu; initdb -D /tmp/pg -U postgres --no-instructions >/dev/null; '
               'pg_ctl -D /tmp/pg -o "-c listen_addresses= -c unix_socket_directories=/tmp" '
               '-w start >/dev/null; '
               'psql -XAt -h /tmp -U postgres -d postgres -v ON_ERROR_STOP=1 '
               '-f /backup/roles.sql >/dev/null; '
               'createdb -h /tmp -U postgres autopilot; '
               'pg_restore -h /tmp -U postgres -d autopilot --exit-on-error '
               '--single-transaction /backup/archive.dump; '
               'psql -XAt -h /tmp -U postgres -d autopilot -v ON_ERROR_STOP=1 '
               '-f /backup/manifest.sql; '
               'psql -XAt -h /tmp -U postgres -d autopilot -v ON_ERROR_STOP=1 '
               '-f /backup/owners.sql')
    with tempfile.TemporaryDirectory(prefix='autopilot-manifest-', dir=str(archive.parent)) as folder:
        sql = Path(folder) / 'manifest.sql'
        sql.write_text(MANIFEST, encoding='utf-8')
        os.chmod(sql, 0o600)
        role_sql = Path(folder) / 'roles.sql'
        if any(role not in ROLES for role in roles) or any(
            role not in roles or member not in roles for role, member in memberships
        ):
            raise ValueError('UNEXPECTED_ROLE')
        role_sql.write_text(''.join(f'CREATE ROLE {role} NOLOGIN;\n' for role in sorted(roles)) +
                            ''.join(f'GRANT {role} TO {member};\n' for role, member in memberships), encoding='utf-8')
        os.chmod(role_sql, 0o600)
        owner_sql = Path(folder) / 'owners.sql'
        owner_sql.write_text(OWNERSHIP_SQL + ';\n', encoding='utf-8')
        os.chmod(owner_sql, 0o600)
        os.chown(folder, 0, 999)
        os.chmod(folder, 0o710)
        for source in (sql, role_sql, owner_sql):
            os.chown(source, 0, 999)
            os.chmod(source, 0o640)
        output = execute(['docker', 'run', '--rm', '--network', 'none', '--read-only',
                          '--user', '999:999', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                          '--memory', '2g', '--cpus', '2', '--tmpfs', '/tmp:rw,size=2g,mode=1777',
                          '-v', str(archive.resolve()) + ':/backup/archive.dump:ro',
                          '-v', str(sql.resolve()) + ':/backup/manifest.sql:ro',
                          '-v', str(role_sql.resolve()) + ':/backup/roles.sql:ro',
                          '-v', str(owner_sql.resolve()) + ':/backup/owners.sql:ro',
                          image, 'sh', '-c', command], timeout=1200)
    lines = output.decode().strip().splitlines()
    if not lines:
        raise ValueError('RESTORE_MANIFEST_MISSING')
    if len(lines) < 2:
        raise ValueError('RESTORE_OWNERSHIP_MISSING')
    actual = json.loads(lines[-2])
    actual_owners = json.loads(lines[-1])
    if actual != expected:
        raise ValueError('RESTORE_MANIFEST_MISMATCH')
    if actual_owners != owners:
        raise ValueError('RESTORE_OWNERSHIP_MISMATCH')
    return hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest()


def perform_backup(receipt, dsn, dump_env, run_id, head, route):
    with tempfile.TemporaryDirectory(prefix='autopilot-production-backup-') as directory:
        folder = Path(directory)
        os.chmod(folder, 0o700)
        source, snapshot, database, db_oid, observed, expected, owners, roles, memberships = source_snapshot(dsn)
        try:
            archive = folder / 'archive.dump'
            execute(['pg_dump', '--format=custom', '--file', str(archive),
                     '--snapshot', snapshot], env=dump_env, timeout=1200)
        finally:
            source.rollback()
            source.close()
        os.chmod(archive, 0o600)
        if not 0 < archive.stat().st_size <= MAX_BYTES:
            raise ValueError('BACKUP_SIZE_BOUND')
        object_name = f'production/autopilot/{observed:%Y/%m/%d}/{run_id}-{digest(archive)}.dump'
        if not all(OBJECT.fullmatch(part) for part in object_name.split('/')):
            raise ValueError('OBJECT_KEY_INVALID')
        downloaded = folder / 'downloaded.dump'
        namespace, bucket, archive_sha = upload_download(archive, downloaded, object_name)
        manifest_sha = hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()
        owners_sha = hashlib.sha256(json.dumps(owners, sort_keys=True).encode()).hexdigest()
        restored_sha = drill(downloaded, expected, owners, roles, memberships)
        record = {'format': FORMAT, 'result': 'PASS', 'database': database,
                  'database_oid': db_oid, 'snapshot_id': snapshot,
                  'route_epoch': route['epoch'], 'route_backend': route['backend'],
                  'snapshot_observed_utc': observed.astimezone(timezone.utc).isoformat(),
                  'archive_sha256': archive_sha, 'download_sha256': digest(downloaded),
                  'manifest_sha256': manifest_sha, 'restored_manifest_sha256': restored_sha,
                  'function_ownership_sha256': owners_sha,
                  'object': {'namespace': namespace, 'bucket': bucket, 'name': object_name},
                  'restore_image': required('AUTOPILOT_RESTORE_IMAGE'),
                  'workflow': {'repository': required('GITHUB_REPOSITORY'),
                               'run_id': run_id, 'head_sha': head},
                  'created_utc': datetime.now(timezone.utc).isoformat()}
        return record


def main():
    if len(sys.argv) != 2:
        raise ValueError('USAGE: production_backup.py /secure/receipt.json')
    receipt = Path(sys.argv[1])
    if receipt.exists() or receipt.is_symlink() or not receipt.is_absolute() or not receipt.parent.is_dir():
        raise ValueError('RECEIPT_DESTINATION_UNSAFE')
    parent = receipt.parent.lstat()
    if receipt.parent.is_symlink() or parent.st_uid != 0 or parent.st_mode & 0o077:
        raise ValueError('RECEIPT_DIRECTORY_UNTRUSTED')
    if os.environ.get('GITHUB_REPOSITORY') != 'olegmed1-art/bridge-video-free' or os.environ.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('TRUSTED_MAIN_REQUIRED')
    run_id, head = required('GITHUB_RUN_ID'), required('GITHUB_SHA')
    if not run_id.isdecimal() or not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('WORKFLOW_PROVENANCE_INVALID')
    dsn = required('AUTOPILOT_BACKUP_DSN')
    dump_env = libpq_environment(dsn)
    with pinned_route() as route:
        record = perform_backup(receipt, dsn, dump_env, run_id, head, route)
    with receipt.open('x', encoding='utf-8') as output:
        os.chmod(receipt, 0o600)
        json.dump(record, output, sort_keys=True)
        output.write('\n')
    print(json.dumps({'result': 'PASS', 'receipt': str(receipt),
                      'archive_sha256': record['archive_sha256'], 'production_mutations': False}))


if __name__ == '__main__':
    os.umask(0o077)
    try:
        main()
    except Exception as exc:
        print(json.dumps({'result': 'NOT_CONFIRMED', 'error_type': type(exc).__name__,
                          'phase': 'backup_or_restore'}), file=sys.stderr)
        sys.exit(2)
