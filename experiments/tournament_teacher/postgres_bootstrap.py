"""Apply unchanged repository migrations inside the official disposable CI service."""
import json
import re
import subprocess
import sys
from .candidate_package import package


def main():
    package()  # Reject a different 0200 migration or rule snapshot before DB work.
    cid = sys.argv[1]
    if not re.fullmatch(r"[0-9a-f]{12,64}",cid):
        raise ValueError("Expected disposable Actions service container ID")
    docker = ["docker", "--host", "unix:///var/run/docker.sock"]
    metadata = json.loads(subprocess.check_output([*docker,"inspect",cid]))[0]
    assert metadata["Config"]["Image"] == "postgres:18"
    assert "POSTGRES_DB=tournament_rehearsal" in metadata["Config"]["Env"]
    assert "POSTGRES_HOST_AUTH_METHOD=trust" in metadata["Config"]["Env"]
    print("Official disposable image:",metadata["Image"])
    def run(*args):
        result = subprocess.run([*docker,*args],text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        if result.returncode:
            print(result.stdout[-12000:])
            raise RuntimeError("Disposable schema bootstrap failed")
        return result.stdout
    run("exec",cid,"psql","-U","postgres","-d","tournament_rehearsal","-v","ON_ERROR_STOP=1","-c","""
        CREATE EXTENSION IF NOT EXISTS pgcrypto;
        CREATE EXTENSION IF NOT EXISTS btree_gist;
        CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
        CREATE ROLE tournament_rehearsal_owner LOGIN CREATEROLE NOSUPERUSER NOCREATEDB NOREPLICATION;
        ALTER DATABASE tournament_rehearsal OWNER TO tournament_rehearsal_owner;
        ALTER SCHEMA public OWNER TO tournament_rehearsal_owner;
    """)
    run("exec",cid,"mkdir","-p","/tmp/tournament-schema")
    run("cp","database",cid+":/tmp/tournament-schema/database")
    run("exec","-w","/tmp/tournament-schema","-e","LC_ALL=C","-e",
        "DATABASE_URL=host=/var/run/postgresql dbname=tournament_rehearsal user=tournament_rehearsal_owner",
        cid,"bash","database/scripts/migrate.sh")
    # Reproduce the existing API principal's independently provisioned READ
    # capability, verified with read-only has_*_privilege probes on 2026-10-04.
    # These grants exist ONLY in this disposable service, never a migration.
    run("exec",cid,"psql","-U","postgres","-d","tournament_rehearsal","-v","ON_ERROR_STOP=1","-c", """
        GRANT USAGE ON SCHEMA ai TO bridge_school_app_principal;
        GRANT SELECT ON ai.decision_position TO bridge_school_app_principal;
    """)
    # Fixture membership permits SET ROLE; no capability is added to app/worker.
    run("exec",cid,"psql","-U","postgres","-d","tournament_rehearsal","-v","ON_ERROR_STOP=1","-c",
        "GRANT bridge_school_worker, bridge_school_app_principal TO tournament_rehearsal_owner WITH INHERIT FALSE, SET TRUE")
    print("Repository migrations applied as non-superuser owner; no production connection.")


if __name__ == "__main__":
    main()
