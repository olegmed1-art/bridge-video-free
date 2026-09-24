"""Hold one validated Neon snapshot open for coordinated pg_dump processes."""
import os
from pathlib import Path
import sys
import time

from ops.oracle_autopilot_source_preflight import connection_parameters


def main():
    import psycopg
    if (len(sys.argv) != 3 or os.environ.get("GITHUB_REPOSITORY") !=
            "olegmed1-art/bridge-video-free" or os.environ.get("GITHUB_REF") != "refs/heads/main"):
        raise ValueError("TRUSTED_MAIN_REQUIRED")
    snapshot_path, done_path = map(Path, sys.argv[1:])
    if snapshot_path.parent != done_path.parent or snapshot_path.parent.stat().st_mode & 0o077:
        raise ValueError("CONTROL_DIRECTORY_FENCE")
    parameters = connection_parameters(os.environ.get("NEON_DATABASE_URL", ""), "neondb_owner")
    parameters["application_name"] = "autopilot-migration-snapshot-keeper"
    with psycopg.connect(**parameters) as connection:
        connection.read_only = True
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute("SELECT pg_export_snapshot()")
            snapshot = cursor.fetchone()[0]
            fd = os.open(snapshot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="ascii") as output:
                output.write(snapshot + "\n")
            deadline = time.monotonic() + 900
            while not done_path.exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("SNAPSHOT_KEEPER_TIMEOUT")
                time.sleep(0.2)
            connection.rollback()


if __name__ == "__main__":
    os.umask(0o077)
    main()
