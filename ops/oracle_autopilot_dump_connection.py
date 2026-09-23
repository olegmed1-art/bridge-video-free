"""Write fixed libpq service files for the trusted autopilot source.

The password is never printed and both files are intended for an ephemeral
GitHub Actions directory with mode 0700.
"""
import os
from pathlib import Path
import sys

from ops.oracle_autopilot_source_preflight import HOST, connection_parameters


def exclusive_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(content)


def pgpass_escape(value):
    return value.replace("\\", "\\\\").replace(":", "\\:")


def main():
    if (len(sys.argv) != 2 or os.environ.get("GITHUB_REPOSITORY") !=
            "olegmed1-art/bridge-video-free" or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("TRUSTED_MAIN_REQUIRED")
    root = Path(sys.argv[1])
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o077:
        raise ValueError("OUTPUT_DIRECTORY_FENCE")
    parameters = connection_parameters(os.environ.get("NEON_DATABASE_URL", ""), "neondb_owner")
    if parameters["host"] != HOST or parameters["dbname"] != "neondb":
        raise ValueError("SOURCE_IDENTITY_FENCE")
    service = """[autopilot_source]
host={host}
port=5432
dbname=neondb
user=neondb_owner
sslmode=verify-full
sslrootcert=/etc/ssl/certs/ca-certificates.crt
channel_binding=require
connect_timeout=10
options=-c default_transaction_read_only=on -c statement_timeout=0 -c lock_timeout=3000
""".format(host=HOST)
    password = pgpass_escape(parameters["password"])
    exclusive_write(root / "pg_service.conf", service)
    exclusive_write(root / ".pgpass", f"{HOST}:5432:neondb:neondb_owner:{password}\n")
    print("PINNED_DIRECT_SOURCE_FILES=READY")


if __name__ == "__main__":
    os.umask(0o077)
    main()
