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
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import psycopg

PROJECT = "misty-poetry-18012774"
PREVIEW = "br-winter-glade-b1sag2el"
PRODUCTION = "br-wispy-lab-b1rq54of"
USER = "bridge_school_worker_principal"
SOURCE_HOST = "ep-wandering-night-b1ej3ow6.c-5.eu-central-1.aws.neon.tech"
TARGET_HOST = "ep-noisy-pine-b1pe30sf.c-5.eu-central-1.aws.neon.tech"
DSN_FILE = Path("/opt/bridge-school/universal-video/secrets/video-queue-dsn")
BACKUP = DSN_FILE.with_name("video-queue-dsn.preview-before-production")
FENCE = Path("/var/lib/bridge-school/issue-881-capacity-lease")
LOCK_FILE = Path("/run/lock/oracle-workload-mutation.lock")


def require(condition):
    if not condition:
        raise RuntimeError("queue_target_guard")


def candidate(raw):
    require(0 < len(raw) <= 4096)
    text = raw.decode("utf-8")
    require(not any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in text))
    p = urlsplit(text)
    require(p.scheme in ("postgres", "postgresql") and not p.fragment)
    require(p.hostname in (SOURCE_HOST, SOURCE_HOST.replace(".c-5", "-pooler.c-5")))
    require(p.username == USER and bool(p.password) and p.path == "/neondb")
    require(p.port in (None, 5432))
    pairs = parse_qsl(p.query, strict_parsing=True)
    require(len(pairs) == len(dict(pairs)))
    require(set(dict(pairs)) <= {"sslmode", "channel_binding"})
    require(dict(pairs).get("sslmode") in ("require", "verify-ca", "verify-full"))
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
                EXISTS (SELECT 1 FROM pg_roles WHERE rolname=current_user
                    AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolbypassrls))""")
            row = cur.fetchone()
            require(row is not None and row[:4] == (PROJECT, branch, "neondb", USER))
            require(row[5] and not row[6])
            if branch == PREVIEW:
                require(not row[4])
            else:
                require(branch == PRODUCTION and row[4])
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
    require(os.geteuid() == 0 and socket.gethostname() == "bridge-school-dds3-frankfurt")
    gid = grp.getgrnam("universal-video").gr_gid
    with open(LOCK_FILE, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(not os.path.lexists(FENCE))
        parent = DSN_FILE.parent.stat()
        require(DSN_FILE.parent.resolve() == DSN_FILE.parent)
        require(parent.st_uid == 0 and not stat.S_IMODE(parent.st_mode) & 0o022)
        old = read_protected(DSN_FILE, gid)
        # A completed repair is checked, never repeated or rolled back implicitly.
        if urlsplit(old.decode("utf-8")).hostname == TARGET_HOST:
            verify(old, PRODUCTION)
            print("QUEUE_TARGET_ALREADY_PRODUCTION verified=true")
            return
        new = candidate(old)
        verify(old, PREVIEW)
        verify(new, PRODUCTION)
        print("QUEUE_TARGET_PREFLIGHT_PASS source=preview target=production idle=true", flush=True)
        if mode == "check":
            return
        require(not os.path.lexists(BACKUP))
        # Root-only rollback copy; never overwrite a prior receipt or backup.
        fd = os.open(BACKUP, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            os.fchown(f.fileno(), 0, 0)
            f.write(old)
            f.flush()
            os.fsync(f.fileno())
        sync_directory(BACKUP.parent)
        require(read_protected(DSN_FILE, gid) == old and not os.path.lexists(FENCE))
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
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "apply"))
    args = parser.parse_args()
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
