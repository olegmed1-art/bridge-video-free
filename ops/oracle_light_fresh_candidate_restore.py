"""Restore a fresh scoped Neon snapshot into a fenced Oracle candidate only."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat as stat_module
import subprocess
import sys

HOST = "autopilot-lite-vnic"
CONTAINER = "bridge-autopilot-postgres"
PORT = "55432"
IMAGE = "sha256:4ef4dbc939d61acea57712655ddb4b4ab27419c913f94cca0cd57cb3ea3c2280"
ROOT = Path("/home/ubuntu/autopilot-db-migration-20260922")
DATABASE_RE = re.compile(r"autopilot_candidate_fresh_[0-9]{6,20}$")
ROLES = (
    "neondb_owner", "autopilot_runtime", "autopilot_runtime_principal",
    "autopilot_callback", "autopilot_callback_login", "autopilot_light_worker_login",
    "bridge_school_reader", "bridge_school_app",
    "bridge_school_worker", "bridge_school_worker_principal",
    "bridge_school_health", "bridge_school_health_principal",
)
MEMBERSHIPS = (
    ("autopilot_runtime", "autopilot_runtime_principal"),
    ("autopilot_runtime_principal", "autopilot_light_worker_login"),
    ("autopilot_callback", "autopilot_callback_login"),
    ("bridge_school_reader", "bridge_school_app"),
    ("bridge_school_app", "bridge_school_worker"),
    ("bridge_school_worker", "bridge_school_worker_principal"),
    ("bridge_school_health", "bridge_school_health_principal"),
)
CREATED_DATABASE = None

def run(*args, input_text=None):
    return subprocess.run(args, input=input_text, check=True, capture_output=True,
                          text=True, timeout=300).stdout.strip()

def sql(database, statement):
    return run("docker", "exec", "-i", "--user", "postgres", CONTAINER,
               "psql", "-XAtq", "-p", PORT, "-U", "postgres", "-d", database,
               "-v", "ON_ERROR_STOP=1", input_text=statement)

def copy_stream_to_container(source, destination, expected_size):
    source.seek(0)
    completed = subprocess.run(
        ("docker", "exec", "-i", "--user", "postgres", CONTAINER,
         "sh", "-ceu",
         'umask 077; set -C; cat > "$1"; test -f "$1"; test ! -L "$1"; '
         'test "$(stat -c "%U:%a" "$1")" = "postgres:600"; stat -c "%s" "$1"',
         "sh", destination),
        stdin=source, check=False, capture_output=True, timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"CONTAINER_STREAM_WRITE_FAILED:rc={completed.returncode}")
    actual_text = completed.stdout.decode("ascii", errors="strict").strip()
    if not actual_text.isdecimal():
        raise RuntimeError("CONTAINER_STREAM_SIZE_REDACTED")
    actual_size = int(actual_text)
    if actual_size != expected_size:
        raise RuntimeError(
            f"CONTAINER_STREAM_SIZE_MISMATCH:expected={expected_size}:actual={actual_size}"
        )

def restore_dump(database, dump_path):
    completed = subprocess.run(
        ("docker", "exec", "--user", "postgres", CONTAINER, "pg_restore",
         "-p", PORT, "-U", "postgres", "-d", database, "--role=neondb_owner",
         "--no-owner", "--single-transaction", "--exit-on-error", dump_path),
        check=False, capture_output=True, text=True, timeout=300,
    )
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout or "").encode("utf-8", errors="replace")
        lowered = diagnostic.lower()
        categories = (
            (b"permission denied", "PERMISSION_DENIED"),
            (b"already exists", "ALREADY_EXISTS"),
            (b"does not exist", "MISSING_OBJECT"),
            (b"unsupported version", "UNSUPPORTED_VERSION"),
            (b"input file appears", "INVALID_INPUT"),
            (b"valid archive", "INVALID_ARCHIVE"),
            (b"end of file", "INPUT_TRUNCATED"),
            (b"could not read from input file", "INPUT_READ_FAILED"),
            (b"could not open input file", "INPUT_OPEN_FAILED"),
            (b"no such file or directory", "INPUT_PATH_MISSING"),
            (b"compression", "COMPRESSION_FAILED"),
            (b"connection to server", "CONNECTION_FAILED"),
            (b"could not execute query", "QUERY_FAILED"),
        )
        category = next((name for marker, name in categories if marker in lowered), "REDACTED")
        raise RuntimeError(
            f"PG_RESTORE_FAILED_{category}:rc={completed.returncode}:bytes={len(diagnostic)}:"
            f"sha256={hashlib.sha256(diagnostic).hexdigest()}"
        )

def ident(value):
    return '"' + value.replace('"', '""') + '"'

def schema_digest_texts(texts):
    digest = hashlib.sha256()
    for text in texts:
        normalized = "\n".join(
            line for line in text.splitlines()
            if not line.startswith("\\restrict ") and not line.startswith("\\unrestrict ")
        ) + "\n"
        digest.update(normalized.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()

def main():
    global CREATED_DATABASE
    if len(sys.argv) != 7:
        raise ValueError("USAGE")
    dump_args = tuple(Path(value) for value in sys.argv[1:4])
    reference_path = Path(sys.argv[4])
    manifest_sql_path = Path(sys.argv[5])
    database = sys.argv[6]
    if run("hostname") != HOST or not os.path.ismount("/srv/autopilot-data"):
        raise ValueError("HOST_OR_VOLUME_FENCE")
    if not DATABASE_RE.fullmatch(database):
        raise ValueError("DATABASE_NAME_FENCE")
    sizes = []
    dumps = []
    dump_sources = []
    for dump in dump_args:
        if dump.parent != ROOT or dump.name in ("", ".", "..") or dump.is_symlink():
            raise ValueError("DUMP_PATH_FENCE")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(dump, flags)
        source = os.fdopen(fd, "rb", buffering=0, closefd=True)
        stat = os.fstat(source.fileno())
        lst = os.lstat(dump)
        if (not stat_module.S_ISREG(stat.st_mode) or (stat.st_dev,stat.st_ino) != (lst.st_dev,lst.st_ino)
                or stat.st_uid != os.getuid() or stat.st_mode & 0o077):
            source.close()
            raise ValueError("DUMP_OWNER_OR_MODE_FENCE")
        size = stat.st_size
        magic = source.read(5)
        if not 0 < size < 256 * 1024 * 1024 or magic != b"PGDMP":
            source.close()
            raise ValueError("DUMP_FORMAT_OR_SIZE")
        sizes.append(size)
        dumps.append(dump)
        dump_sources.append(source)
    sidecars = []
    for path in (reference_path, manifest_sql_path):
        if path.parent != ROOT or path.name in ("", ".", "..") or path.is_symlink():
            raise ValueError("SIDECAR_PATH_FENCE")
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        source = os.fdopen(fd, "r", encoding="utf-8", closefd=True)
        stat = os.fstat(source.fileno())
        lst = os.lstat(path)
        if (not stat_module.S_ISREG(stat.st_mode) or (stat.st_dev,stat.st_ino) != (lst.st_dev,lst.st_ino)
                or stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_size > 1024 * 1024):
            source.close()
            raise ValueError("SIDECAR_OWNER_OR_MODE_FENCE")
        sidecars.append(source.read())
        source.close()
    expected_manifest = json.loads(sidecars[0])
    manifest_sql = sidecars[1]
    for path in (*dumps, reference_path, manifest_sql_path):
        os.unlink(path)
    directory_fd = os.open(ROOT, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    target = json.loads(run("docker", "inspect", CONTAINER))[0]
    if (target["Image"] != IMAGE or target["HostConfig"]["NetworkMode"] != "host"
            or target["HostConfig"]["PortBindings"]
            or target["Config"]["Labels"].get("managed_by") != "bridge-autopilot-pg-stage-v1"
            or not any(m["Source"] == "/srv/autopilot-data/postgresql"
                       and m["Destination"] == "/var/lib/postgresql"
                       for m in target["Mounts"])):
        raise ValueError("TARGET_CONTAINER_FENCE")
    if sql("postgres", f"SELECT count(*) FROM pg_database WHERE datname='{database}';") != "0":
        raise ValueError("CANDIDATE_ALREADY_EXISTS")
    role_list = ",".join("'" + role + "'" for role in ROLES)
    safe_roles = sql("postgres", f"SELECT count(*) FROM pg_authid WHERE rolname IN ({role_list}) AND "
                 "NOT rolcanlogin AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole "
                 "AND NOT rolreplication AND NOT rolbypassrls AND rolinherit AND rolpassword IS NULL;")
    if safe_roles != str(len(ROLES)):
        raise ValueError("APPLICATION_ROLE_FENCE")
    expected_memberships = [list(item) + [False, True, True] for item in sorted(MEMBERSHIPS)]
    actual_memberships = json.loads(sql("postgres",
        "SELECT coalesce(jsonb_agg(jsonb_build_array(g.rolname,m.rolname,a.admin_option,"
        "a.inherit_option,a.set_option) ORDER BY g.rolname,m.rolname),'[]') "
        "FROM pg_auth_members a JOIN pg_roles m ON m.oid=a.member JOIN pg_roles g ON g.oid=a.roleid "
        f"WHERE (m.rolname IN ({role_list}) OR g.rolname IN ({role_list}));"))
    if actual_memberships != expected_memberships:
        raise ValueError("ROLE_MEMBERSHIP_FENCE")
    qdb = ident(database)
    container_temp = []
    database_created = False
    try:
        sql("postgres", f"CREATE DATABASE {qdb} OWNER neondb_owner TEMPLATE template0 "
            "LOCALE_PROVIDER builtin LOCALE 'C.UTF-8' ALLOW_CONNECTIONS false CONNECTION LIMIT 0;")
        database_created = True
        CREATED_DATABASE = database
        sql("postgres", f"REVOKE ALL PRIVILEGES ON DATABASE {qdb} FROM PUBLIC;")
        fence = json.loads(sql("postgres",
            "SELECT jsonb_build_array(datallowconn,datconnlimit,pg_get_userbyid(datdba),"
            "datlocprovider,datlocale,(SELECT count(*) FROM aclexplode(coalesce(datacl,"
            "acldefault('d',datdba))) a WHERE a.grantee<>datdba)) "
            f"FROM pg_database WHERE datname='{database}';"))
        if fence != [False, 0, "neondb_owner", "b", "C.UTF-8", 0]:
            raise ValueError("INITIAL_DATABASE_FENCE")
        sql("postgres", f"ALTER DATABASE {qdb} ALLOW_CONNECTIONS true;")
        sql(database, "CREATE EXTENSION pgcrypto; CREATE EXTENSION btree_gist;")
        for index, source in enumerate(dump_sources):
            remote_dump = f"/tmp/{database}-{index}.dump"
            container_temp.append(remote_dump)
            copy_stream_to_container(source, remote_dump, sizes[index])
            restore_dump(database, remote_dump)
    except Exception:
        if database_created:
            sql("postgres", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{database}';")
            sql("postgres", f"ALTER DATABASE {qdb} ALLOW_CONNECTIONS false;")
        raise
    finally:
        cleanup_failed = False
        for remote_dump in container_temp:
            removed = subprocess.run(("docker", "exec", CONTAINER, "rm", "-f", remote_dump),
                                     check=False, capture_output=True, text=True, timeout=30)
            absent = subprocess.run(("docker", "exec", CONTAINER, "test", "!", "-e", remote_dump),
                                    check=False, capture_output=True, text=True, timeout=30)
            cleanup_failed = cleanup_failed or removed.returncode != 0 or absent.returncode != 0
        if cleanup_failed:
            raise RuntimeError("CONTAINER_TEMP_CLEANUP_NOT_CONFIRMED")
    counts = json.loads(sql(database,
        "SELECT jsonb_build_object("
        "'tables',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname IN ('autopilot','autopilot_reconcile') AND c.relkind IN ('r','p')),"
        "'functions',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname IN ('autopilot','autopilot_reconcile')),"
        "'health_rows',(SELECT count(*) FROM public.autopilot_operational_health_signal),"
        "'school_schemas',(SELECT count(*) FROM pg_namespace WHERE nspname IN "
        "('assistant_lab','school_knowledge','knowledge')));"))
    if counts["tables"] != 64 or counts["functions"] < 90 or counts["health_rows"] != 3:
        raise ValueError("RESTORE_CONTENT_CONTRACT")
    if counts["school_schemas"] != 0:
        raise ValueError("SCHOOL_KNOWLEDGE_SCOPE_VIOLATION")
    actual_manifest = json.loads(sql(database, manifest_sql))
    schema_texts = [
        run("docker", "exec", "--user", "postgres", CONTAINER, "pg_dump",
            "-p", PORT, "-U", "postgres", "-d", database, "--schema-only",
            "--no-owner", "--schema=autopilot", "--schema=autopilot_reconcile"),
        run("docker", "exec", "--user", "postgres", CONTAINER, "pg_dump",
            "-p", PORT, "-U", "postgres", "-d", database, "--schema-only",
            "--no-owner", "--table=public.schema_migration"),
        run("docker", "exec", "--user", "postgres", CONTAINER, "pg_dump",
            "-p", PORT, "-U", "postgres", "-d", database, "--schema-only",
            "--no-owner", "--table=public.autopilot_operational_health_signal"),
    ]
    actual_manifest["schema_sha256"] = schema_digest_texts(schema_texts)
    actual_manifest["schema_parts_sha256"] = [schema_digest_texts([text]) for text in schema_texts]
    if actual_manifest != expected_manifest:
        expected_entries = expected_manifest.get("entries", {})
        actual_entries = actual_manifest.get("entries", {})
        differing = {
            key for key in set(expected_entries) | set(actual_entries)
            if expected_entries.get(key) != actual_entries.get(key)
        }
        categories = set()
        fixed = {
            "functions": "FUNCTIONS", "objects": "OBJECTS", "schemas": "SCHEMAS",
            "extensions": "EXTENSIONS", "unexpected_schemas": "UNEXPECTED_SCHEMAS",
            "health_view": "HEALTH_VIEW",
            "security_definer_superowner": "SECURITY_DEFINER",
        }
        for key in differing:
            if key.startswith("data:"):
                categories.add("DATA")
            elif key.startswith("sequence:"):
                categories.add("SEQUENCES")
            else:
                categories.add(fixed.get(key, "OTHER_ENTRY"))
        if actual_manifest.get("schema_sha256") != expected_manifest.get("schema_sha256"):
            categories.add("SCHEMA_DIGEST")
        expected_parts = expected_manifest.get("schema_parts_sha256", [])
        actual_parts = actual_manifest.get("schema_parts_sha256", [])
        part_labels = ("SCHEMA_CORE", "SCHEMA_LEDGER", "SCHEMA_HEALTH")
        for index, label in enumerate(part_labels):
            if (index >= len(expected_parts) or index >= len(actual_parts)
                    or expected_parts[index] != actual_parts[index]):
                categories.add(label)
        if actual_manifest.get("format") != expected_manifest.get("format"):
            categories.add("FORMAT")
        if actual_manifest.get("scope") != expected_manifest.get("scope"):
            categories.add("SCOPE")
        raise ValueError(
            "SOURCE_TARGET_MANIFEST_MISMATCH:categories=" + ",".join(sorted(categories))
            + f":entry_count={len(differing)}"
        )
    sql("postgres", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{database}';")
    sql("postgres", f"ALTER DATABASE {qdb} ALLOW_CONNECTIONS false;")
    owner = json.loads(sql("postgres",
        "SELECT jsonb_build_array(datallowconn,datconnlimit,pg_get_userbyid(datdba),datlocprovider,datlocale,"
        "(SELECT count(*) FROM aclexplode(coalesce(datacl,acldefault('d',datdba))) a "
        f"WHERE a.grantee<>datdba)) FROM pg_database WHERE datname='{database}';"))
    if owner != [False, 0, "neondb_owner", "b", "C.UTF-8", 0]:
        raise ValueError("DATABASE_FENCE")
    digests = []
    for source in dump_sources:
        digest = hashlib.sha256()
        source.seek(0)
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
        digests.append(digest.hexdigest())
        source.close()
    print(json.dumps({"candidate": "VERIFIED_FENCED", "database": database,
        "dump_sha256": digests, "dump_bytes": sizes, **counts,
        "manifest_entries": len(actual_manifest["entries"]),
        "application_roles_login": False, "production_cutover": False,
        "neon_changed": False}, sort_keys=True))

if __name__ == "__main__":
    try:
        os.umask(0o077)
        main()
    except Exception as exc:
        fence_status = "NOT_NEEDED"
        if CREATED_DATABASE is not None:
            try:
                qdb = ident(CREATED_DATABASE)
                sql("postgres", f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='{CREATED_DATABASE}';")
                sql("postgres", f"ALTER DATABASE {qdb} ALLOW_CONNECTIONS false CONNECTION LIMIT 0;")
                state = sql("postgres", f"SELECT (NOT datallowconn AND datconnlimit=0)::text FROM pg_database WHERE datname='{CREATED_DATABASE}';")
                if state != "true":
                    raise ValueError("REFENCE_NOT_CONFIRMED")
                fence_status = "CLOSED"
            except Exception:
                fence_status = "UNSAFE_STATE"
        print(json.dumps({"candidate": "NOT_CONFIRMED", "error_type": type(exc).__name__,
                          "failure_fence": fence_status}))
        raise
