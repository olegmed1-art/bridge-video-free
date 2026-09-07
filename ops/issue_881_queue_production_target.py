#!/usr/bin/env python3
"""Check or repair the fixed host queue target without exposing credentials."""
import argparse
import fcntl
import grp
import os
from pathlib import Path
import re
import socket
import stat
import tempfile
import time
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import psycopg

PROJECT = "misty-poetry-18012774"
PREVIEW = "br-winter-glade-b1sag2el"
PRODUCTION = "br-wispy-lab-b1rq54of"
USER = "bridge_school_worker_principal"
SOURCE_HOST = "ep-wandering-night-b1ej3ow6.c-5.eu-central-1.aws.neon.tech"
TARGET_HOST = "ep-noisy-pine-b1pe30sf-pooler.c-5.eu-central-1.aws.neon.tech"
DSN_FILE = Path("/opt/bridge-school/universal-video/secrets/video-queue-dsn")
BACKUP = DSN_FILE.with_name("video-queue-dsn.preview-before-production")
FENCE = Path("/var/lib/bridge-school/issue-881-capacity-lease")
LOCK_FILE = Path("/run/lock/oracle-workload-mutation.lock")
OWNER_ATTESTATION_DEADLINE = 0
ALLOWED_QUEUE_FUNCTIONS = [
    'claim_job(text, integer, text, text)',
    'enqueue_drive_batch(text, text, text, text, text, text, text, text, jsonb)',
    'finish_job(uuid, uuid, text, text, jsonb, text)',
    'heartbeat_job(uuid, uuid, text, integer)',
    'precanary_idle_snapshot()',
    'retry_job(uuid, uuid, text, text, integer, integer)',
]
QUEUE_ACL_SQL = """SELECT NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='video_queue' AND CASE
        WHEN c.relkind IN ('r','p','f') THEN
            has_table_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR has_any_column_privilege(current_user,c.oid,'SELECT,INSERT,UPDATE,REFERENCES')
            OR CASE WHEN current_setting('server_version_num')::integer >= 170000
                THEN has_table_privilege(current_user,c.oid,'MAINTAIN') ELSE false END
        WHEN c.relkind IN ('v','m') THEN
            has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')
            OR CASE WHEN current_setting('server_version_num')::integer >= 170000
                THEN has_table_privilege(current_user,c.oid,'MAINTAIN') ELSE false END
        WHEN c.relkind='S' THEN has_sequence_privilege(current_user,c.oid,'USAGE,SELECT,UPDATE')
        ELSE false END)
    AND has_table_privilege(current_user,'video_queue.batch_status','SELECT')
    AND has_table_privilege(current_user,'video_queue.job_status','SELECT') AS base_acl_safe,
    (SELECT array_agg(p.proname || '(' || oidvectortypes(p.proargtypes) || ')' ORDER BY p.proname, oidvectortypes(p.proargtypes))
     FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
     WHERE n.nspname='video_queue' AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS executable_functions,
    (SELECT array_agg(c.relname ORDER BY c.relname)
     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='video_queue' AND c.relkind IN ('v','m')
       AND (has_table_privilege(current_user,c.oid,'SELECT')
            OR has_any_column_privilege(current_user,c.oid,'SELECT'))) AS readable_views"""
# Drift fingerprints from the independently exercised 0056-0058 rehearsal
# (source main 6b20bd9e, expanded migration SHA256 b203bc82546dbbb81bb5d1e8e7099e1731e2d628a66b1d54d2784b885fe575e0).
EXPECTED_QUEUE_VERSION = ('a5eb36f8b89facad2dc49a5c3d13fa4f', 'c738d3e6c8ecdf53b12b04516ac8ea89', 3,
                          'c7fd96744ef2138f74310af4917470a1', '1036e807796d9422f9ba968a1bb07087')
QUEUE_VERSION_SQL = """SELECT
    (SELECT md5(string_agg(pg_get_functiondef(p.oid) || ' OWNER=' || pg_get_userbyid(p.proowner),
        E'\\n' ORDER BY p.proname, oidvectortypes(p.proargtypes)))
     FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='video_queue'),
    (SELECT md5(string_agg(c.relname || ':' || k.conname || ':' || pg_get_constraintdef(k.oid)
        || ':' || k.convalidated::text, E'\\n' ORDER BY c.relname,k.conname))
     FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
     JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'),
    (SELECT count(*) FROM public.schema_migration WHERE migration_key IN
        ('0056_universal_video_queue','0057_universal_video_canary_review_gate','0058_universal_video_terminal_v2_gate')),
    (SELECT md5(string_agg(c.relname || ':' || pg_get_viewdef(c.oid,false) || ':OWNER='
        || pg_get_userbyid(c.relowner) || ':OPTIONS=' || coalesce(c.reloptions::text,''), E'\\n' ORDER BY c.relname))
     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     WHERE n.nspname='video_queue' AND c.relkind IN ('v','m')),
    (SELECT md5(string_agg(jsonb_build_array(c.relname,c.relkind,pg_get_userbyid(c.relowner),
        c.relrowsecurity,c.relforcerowsecurity,c.reloptions,a.attnum,a.attname,
        format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,a.attisdropped,
        coalesce(pg_get_expr(d.adbin,d.adrelid),''),coalesce(coll.collname,''))::text,
        E'\\n' ORDER BY c.relname,a.attnum))
     FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
     JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0
     LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
     LEFT JOIN pg_collation coll ON coll.oid=a.attcollation
     WHERE n.nspname='video_queue' AND c.relkind IN ('r','p'))"""


EXPECTED_QUEUE_SECURITY = ('a71e6b1e0161c6491106937515d7cf67', '3de2b08011aec2dbe22ef4d815f530b9')
# Canonical full ACLs and triggers; normalize only generated internal trigger names.
QUEUE_SECURITY_SQL = """SELECT
(SELECT md5(coalesce(string_agg(jsonb_build_array(p.proname,oidvectortypes(p.proargtypes),pg_get_userbyid(a.grantor),CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type,a.is_grantable)::text,E'\\n' ORDER BY p.proname,oidvectortypes(p.proargtypes),pg_get_userbyid(a.grantor),CASE WHEN a.grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END,a.privilege_type,a.is_grantable),'')) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='video_queue') AS function_acl,
(SELECT md5(coalesce(string_agg(jsonb_build_array(c.relname,t.tgisinternal,CASE WHEN t.tgisinternal THEN replace(pg_get_triggerdef(t.oid),quote_ident(t.tgname),'INTERNAL') ELSE pg_get_triggerdef(t.oid) END,t.tgenabled,pg_get_userbyid(c.relowner),pg_get_functiondef(p.oid),pg_get_userbyid(p.proowner))::text,E'\\n' ORDER BY c.relname,CASE WHEN t.tgisinternal THEN replace(pg_get_triggerdef(t.oid),quote_ident(t.tgname),'INTERNAL') ELSE pg_get_triggerdef(t.oid) END),'')) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid WHERE n.nspname='video_queue') AS triggers"""


EXPECTED_QUEUE_OBJECT_SECURITY = ('9cedb5db7b79596362f52d28442c105c', 0)
QUEUE_OBJECT_SECURITY_SQL = """WITH grants AS (
SELECT 'schema'::text AS kind,n.nspname::text AS object,''::text AS column_name,n.nspowner AS owner,a.*
FROM pg_namespace n CROSS JOIN LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a WHERE n.nspname='video_queue'
UNION ALL SELECT c.relkind::text,c.relname::text,'',c.relowner,a.* FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault(CASE WHEN c.relkind='S' THEN 'S'::"char" ELSE 'r'::"char" END,c.relowner))) a WHERE n.nspname='video_queue'
UNION ALL SELECT 'column',c.relname::text,t.attname::text,c.relowner,a.* FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute t ON t.attrelid=c.oid CROSS JOIN LATERAL aclexplode(t.attacl) a WHERE n.nspname='video_queue')
SELECT md5(coalesce(string_agg(jsonb_build_array(kind,object,column_name,pg_get_userbyid(owner),pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee) END,privilege_type,is_grantable)::text,E'\\n' ORDER BY kind,object,column_name,pg_get_userbyid(grantor),CASE WHEN grantee=0 THEN 'PUBLIC' ELSE pg_get_userbyid(grantee) END,privilege_type,is_grantable),'')) AS object_acl,
(SELECT count(*) FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue' AND NOT(c.relkind IN ('v','m') AND r.rulename='_RETURN' AND r.ev_type='1')) AS unexpected_rules
FROM grants"""


EXPECTED_QUEUE_CAPABILITIES = ('45ef07b83a827ac8a7cada50cc15a52a', '9fa28331076bacd831ddf6f9ecab15a0',
                               'e2b009cc6c12ecd7dfc7325c359f87fd')
QUEUE_CAPABILITIES_SQL = """WITH RECURSIVE caps(oid) AS (
SELECT DISTINCT a.grantee FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace CROSS JOIN LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname='video_queue' AND a.grantee<>0
UNION SELECT m.member FROM pg_auth_members m JOIN caps c ON c.oid=m.roleid),
roles AS (SELECT r.* FROM pg_roles r JOIN caps c ON c.oid=r.oid)
SELECT
(SELECT md5(coalesce(string_agg(jsonb_build_array(parent.rolname,child.rolname,pg_get_userbyid(m.grantor),m.admin_option,m.inherit_option,m.set_option)::text,E'\\n' ORDER BY parent.rolname,child.rolname,pg_get_userbyid(m.grantor)),'')) FROM pg_auth_members m JOIN roles parent ON parent.oid=m.roleid JOIN pg_roles child ON child.oid=m.member) AS capability_members,
(SELECT md5(coalesce(string_agg(jsonb_build_array(rolname,rolsuper,rolinherit,rolcreaterole,rolcreatedb,rolcanlogin,rolreplication,rolbypassrls,rolconfig,rolvaliduntil::text,rolconnlimit)::text,E'\\n' ORDER BY rolname),'')) FROM roles) AS capability_attributes,
(SELECT md5(coalesce(string_agg(jsonb_build_array(c.relname,pg_get_indexdef(c.oid),i.indisvalid,i.indisready,i.indislive,i.indisunique,i.indisprimary,i.indisexclusion,i.indimmediate,i.indisreplident,c.reloptions,pg_get_userbyid(c.relowner))::text,E'\\n' ORDER BY c.relname),'')) FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue') AS indexes"""


# Mutable sequence position requires the separate owner read in the runbook;
# the worker deliberately has no SELECT/USAGE on the sequence or base table.
QUEUE_SEQUENCE_SQL = """SELECT count(*)=1 AND NOT EXISTS (
    SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='video_queue' AND c.relpersistence <> 'p') AND bool_and(
    c.relname='job_event_event_id_seq' AND s.seqtypid='bigint'::regtype
    AND s.seqstart=1 AND s.seqincrement=1 AND s.seqmin=1
    AND s.seqmax=9223372036854775807 AND s.seqcache=1 AND NOT s.seqcycle)
    FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid
    JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='video_queue'"""


EXPECTED_QUEUE_DIGEST = ('a6a529fa70af423db2b3c1f5086b4594', '9fea6e3ad1042481f869b8eeb5ab9f8d',
    '7eef740b104b09c5ce4d3ace106089908883ec8203eb2472c2b67f5cc137325c')
QUEUE_DIGEST_SQL = """SELECT
(SELECT md5(string_agg(pg_get_functiondef(p.oid)||':OWNER='||pg_get_userbyid(p.proowner),E'\\n' ORDER BY oidvectortypes(p.proargtypes))) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.proname='digest'),
(SELECT md5(jsonb_build_array(e.extname,e.extversion,pg_get_userbyid(e.extowner),n.nspname)::text) FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='pgcrypto'),
encode(public.digest(convert_to('issue881','UTF8'),'sha256'),'hex')"""


def require(condition):
    if not condition:
        raise RuntimeError("queue_target_guard")


def validated_url(raw, hosts):
    require(0 < len(raw) <= 4096)
    text = raw.decode("utf-8")
    require(not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text))
    p = urlsplit(text)
    require(p.scheme in ("postgres", "postgresql") and not p.fragment)
    require(p.hostname in hosts)
    require(p.username == USER and bool(p.password) and p.path == "/neondb")
    require(p.port in (None, 5432))
    # Retain blank values so an override such as ``host=`` or a blank duplicate
    # cannot disappear before the exact query-key allowlist is enforced.
    pairs = parse_qsl(p.query, strict_parsing=True, keep_blank_values=True)
    require(len(pairs) == len(dict(pairs)))
    require(set(dict(pairs)) <= {"sslmode", "channel_binding"})
    require(dict(pairs).get("sslmode") in ("require", "verify-ca", "verify-full"))
    require(dict(pairs).get("channel_binding") == "require")
    return p


def candidate(raw):
    p = validated_url(raw, (SOURCE_HOST, SOURCE_HOST.replace(".c-5", "-pooler.c-5")))
    # Preserve raw escaped credentials, TLS options and database. No host scan,
    # endpoint guessing, role changes, or credential retrieval from other files.
    authority = p.netloc.rsplit("@", 1)[0] + "@" + TARGET_HOST + ":5432"
    return urlunsplit((p.scheme, authority, p.path, p.query, "")).encode("utf-8")


def verify(raw, branch):
    with psycopg.connect(raw.decode("utf-8"), connect_timeout=8,
                         application_name="issue881-queue-target") as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='10s'")
            cur.execute("SET LOCAL lock_timeout='2s'")
            cur.execute("""SELECT current_setting('neon.project_id',true),
                current_setting('neon.branch_id',true), current_database(), current_user,
                EXISTS (SELECT 1 FROM pg_namespace WHERE nspname='video_queue'),
                pg_has_role(current_user,'bridge_school_worker','MEMBER'),
                EXISTS (SELECT 1 FROM pg_roles WHERE (rolname=current_user OR pg_has_role(current_user,oid,'MEMBER'))
                    AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)),
                (SELECT array_agg(rolname ORDER BY rolname) FROM pg_roles
                    WHERE rolname <> current_user AND pg_has_role(current_user,oid,'MEMBER')),
                EXISTS (SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
                    WHERE (r.rolname=current_user OR pg_has_role(current_user,r.oid,'MEMBER')) AND m.admin_option),
                (SELECT array_agg(parent.rolname ORDER BY parent.rolname)
                    FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid
                    JOIN pg_roles child ON child.oid=m.member WHERE child.rolname=current_user),
                (NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname IN ('public','video_queue')
                    AND has_schema_privilege(current_user,oid,'CREATE'))
                 AND NOT has_table_privilege(current_user,'public.person','INSERT,DELETE')
                 AND has_table_privilege(current_user,'public.source_observation','INSERT')
                 AND NOT has_table_privilege(current_user,'public.source_observation','UPDATE,DELETE')
                 AND NOT has_table_privilege(current_user,'public.operational_health_policy','UPDATE')
                 AND has_table_privilege(current_user,'public.school','SELECT'))""")
            row = cur.fetchone()
            require(row is not None and row[:4] == (PROJECT, branch, "neondb", USER))
            require(row[5] and not row[6])
            # 0005 defines worker -> app -> reader; 0016 gives the principal
            # exactly one direct membership in worker. Preserve that hierarchy.
            require(row[7] == ['bridge_school_app', 'bridge_school_reader', 'bridge_school_worker'])
            require(not row[8] and row[9] == ['bridge_school_worker'])
            if branch == PREVIEW:
                require(not row[4])
            else:
                require(branch == PRODUCTION and row[4])
                # The source preview predates production onboarding ACL fixes.
                # Only the destination credential is being installed; enforce
                # the current production object-privilege contract there.
                require(row[10])
                cur.execute(QUEUE_ACL_SQL)
                require(cur.fetchone() == (True, ALLOWED_QUEUE_FUNCTIONS, ['batch_status', 'job_status']))
                cur.execute(QUEUE_VERSION_SQL)
                require(cur.fetchone() == EXPECTED_QUEUE_VERSION)
                cur.execute(QUEUE_SECURITY_SQL)
                require(cur.fetchone() == EXPECTED_QUEUE_SECURITY)
                cur.execute(QUEUE_OBJECT_SECURITY_SQL)
                require(cur.fetchone() == EXPECTED_QUEUE_OBJECT_SECURITY)
                cur.execute(QUEUE_CAPABILITIES_SQL)
                require(cur.fetchone() == EXPECTED_QUEUE_CAPABILITIES)
                cur.execute(QUEUE_SEQUENCE_SQL)
                require(cur.fetchone() == (True,))
                cur.execute(QUEUE_DIGEST_SQL)
                require(cur.fetchone() == EXPECTED_QUEUE_DIGEST)
                cur.execute("SELECT * FROM video_queue.precanary_idle_snapshot()")
                require(cur.fetchone() == (0, 0))


def read_protected(path, gid, mode=0o640):
    require(path.resolve() == path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_gid == gid)
        require(stat.S_IMODE(s.st_mode) == mode and s.st_nlink == 1)
        data = f.read(4097)
        require(0 < len(data) <= 4096)
        return data


def sync_directory(path):
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def replace_protected(path, raw, gid):
    fd, name = tempfile.mkstemp(prefix=".queue-target-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchown(f.fileno(), 0, gid)
            os.fchmod(f.fileno(), 0o640)
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(mode):
    require(time.time() < OWNER_ATTESTATION_DEADLINE <= time.time() + 300)
    require(os.geteuid() == 0 and socket.gethostname() == "bridge-school-dds3-frankfurt")
    gid = grp.getgrnam("universal-video").gr_gid
    require(LOCK_FILE.resolve() == LOCK_FILE)
    fd = os.open(LOCK_FILE, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as lock:
        metadata = os.fstat(lock.fileno())
        require(stat.S_ISREG(metadata.st_mode) and metadata.st_uid == 0)
        require(metadata.st_nlink == 1 and not stat.S_IMODE(metadata.st_mode) & 0o022)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not os.path.lexists(FENCE))
        parent = DSN_FILE.parent.stat()
        require(DSN_FILE.parent.resolve() == DSN_FILE.parent)
        require(parent.st_uid == 0 and not stat.S_IMODE(parent.st_mode) & 0o022)
        old = read_protected(DSN_FILE, gid)
        # A completed repair is checked, never repeated or rolled back implicitly.
        if urlsplit(old.decode("utf-8")).hostname == TARGET_HOST:
            validated_url(old, (TARGET_HOST,))
            rollback = read_protected(BACKUP, 0, 0o600)
            require(candidate(rollback) == old)
            verify(rollback, PREVIEW)
            verify(old, PRODUCTION)
            print("QUEUE_TARGET_ALREADY_PRODUCTION verified=true")
            return
        new = candidate(old)
        require(not os.path.lexists(BACKUP))
        verify(old, PREVIEW)
        verify(new, PRODUCTION)
        require(time.time() < OWNER_ATTESTATION_DEADLINE)
        print("QUEUE_TARGET_PREFLIGHT_PASS source=preview target=production idle=true", flush=True)
        if mode == "check":
            return
        require(not os.path.lexists(BACKUP))
        # Root-only rollback copy; never overwrite a prior receipt or backup.
        fd = os.open(BACKUP, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            backup_identity = os.fstat(f.fileno())
            os.fchown(f.fileno(), 0, 0)
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        try:
            sync_directory(BACKUP.parent)
            require(read_protected(DSN_FILE, gid) == old and not os.path.lexists(FENCE))
            require(time.time() < OWNER_ATTESTATION_DEADLINE)
        except Exception:
            # Before replacement starts, remove only this attempt's intact copy.
            current_backup = BACKUP.lstat()
            if ((current_backup.st_dev, current_backup.st_ino) ==
                    (backup_identity.st_dev, backup_identity.st_ino)
                    and read_protected(BACKUP, 0, 0o600) == old):
                BACKUP.unlink()
                sync_directory(BACKUP.parent)
            raise
        try:
            replace_protected(DSN_FILE, new, gid)
            require(read_protected(DSN_FILE, gid) == new)
            verify(new, PRODUCTION)
        except Exception:
            # Only restore our own candidate, never overwrite an external change.
            if read_protected(DSN_FILE, gid) == new:
                replace_protected(DSN_FILE, old, gid)
                print("QUEUE_TARGET_ROLLED_BACK", flush=True)
            raise
        print("QUEUE_TARGET_APPLY_PASS target=production host_file_verified=true container_recreated=false")


def main():
    global OWNER_ATTESTATION_DEADLINE
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "apply"))
    parser.add_argument("owner_attestation_deadline", type=int)
    args = parser.parse_args()
    OWNER_ATTESTATION_DEADLINE = args.owner_attestation_deadline
    try:
        run(args.mode)
        return 0
    except Exception as exc:
        code = getattr(exc, "sqlstate", None)
        code = code if isinstance(code, str) and re.fullmatch(r"[0-9A-Z]{5}", code) else "none"
        print(f"QUEUE_TARGET_ERROR class={type(exc).__name__} sqlstate={code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
