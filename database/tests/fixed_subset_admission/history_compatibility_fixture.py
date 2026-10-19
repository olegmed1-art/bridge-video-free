"""Exercise both immutable 0060 ledger histories in disposable localhost PG18.

The legacy ledger fixture checks inventory and explicit recovery refusal. It does
not pretend that a new-candidate catalog is equivalent to the installed legacy SQL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import psycopg

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "database/recovery/fixed_subset"))
import connector_packet as packet

ADMIN = "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
KEY = "0060_fixed_subset_admission"


def run_migrator(db_name: str, migration_dir: Path, manifest: Path):
    env = os.environ.copy()
    env.update(DATABASE_URL=f"postgresql://postgres:postgres@127.0.0.1:5432/{db_name}",
               MIGRATIONS_DIR=migration_dir.as_posix(),
               EXTERNAL_HISTORY_FILE=manifest.as_posix())
    return subprocess.run(["bash", "database/scripts/migrate.sh"], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=90)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print("Offline; --execute requires a disposable localhost PostgreSQL 18")
        return
    assert args.evidence and not any(os.environ.get(k) for k in
        ("DATABASE_URL", "NEON_API_KEY", "PGSERVICE", "PGHOST", "PGPASSWORD"))
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=True)
    results = []
    with psycopg.connect(ADMIN, connect_timeout=5) as admin:
        admin.autocommit = True
        assert 180000 <= int(admin.execute("SHOW server_version_num").fetchone()[0]) < 190000
        for version, checksum in packet.INSTALLED_0060_VERSIONS.items():
            db_name = "subset_history_legacy" if checksum == "legacy-744d068a" else "subset_history_new"
            assert admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,)).fetchone() is None
            admin.execute(f"CREATE DATABASE {db_name}")
            try:
                with tempfile.TemporaryDirectory(prefix="subset-history-") as tmp:
                    staging = Path(tmp) / "migrations"
                    staging.mkdir()
                    source_0059 = next((ROOT / "database/migrations").glob("0059_*.sql"))
                    shutil.copyfile(source_0059, staging / source_0059.name)
                    with psycopg.connect(f"postgresql://postgres:postgres@127.0.0.1:5432/{db_name}") as db:
                        db.execute("CREATE TABLE schema_migration(migration_key text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now(), checksum text)")
                        db.execute("INSERT INTO schema_migration(migration_key,checksum) VALUES(%s,%s),(%s,%s)",
                                   (source_0059.stem, hashlib.sha256(source_0059.read_bytes()).hexdigest(), KEY, version))
                    manifest = ROOT / "database/external_migration_history.tsv"
                    good = run_migrator(db_name, staging, manifest)
                    assert good.returncode == 0, good.stderr
                    assert f"Verified audited external migration history: {KEY} [{checksum}]" in good.stdout
                    assert "Applying migration" not in good.stdout
                    if checksum == "legacy-744d068a":
                        for builder in (packet.verify_payload, packet.recover_payload):
                            try:
                                builder({}, installed_0060_checksum=version)
                            except ValueError as exc:
                                assert str(exc) == "LEGACY_0060_REQUIRES_PINNED_744d068a_VERIFIER_OR_RECOVERY"
                            else:
                                raise AssertionError("legacy recovery was not refused")
                    for suffix in ("\r", "0" * 64):
                        with psycopg.connect(f"postgresql://postgres:postgres@127.0.0.1:5432/{db_name}") as db:
                            db.execute("UPDATE schema_migration SET checksum=%s WHERE migration_key=%s", (version + suffix if suffix == "\r" else suffix, KEY))
                        bad = run_migrator(db_name, staging, manifest)
                        assert bad.returncode != 0 and f"External migration checksum mismatch: {KEY}" in bad.stderr
                        with psycopg.connect(f"postgresql://postgres:postgres@127.0.0.1:5432/{db_name}") as db:
                            db.execute("UPDATE schema_migration SET checksum=%s WHERE migration_key=%s", (version, KEY))
                    with psycopg.connect(f"postgresql://postgres:postgres@127.0.0.1:5432/{db_name}") as db:
                        rows = db.execute("SELECT migration_key,checksum FROM schema_migration ORDER BY migration_key").fetchall()
                        assert len(rows) == 2 and rows[-1] == (KEY, version)
                    results.append({"version": checksum, "checksum": version, "inventory": "PASS",
                                    "recovery": "FAIL_CLOSED" if checksum == "legacy-744d068a" else "NEW_PATH_SEPARATELY_TESTED",
                                    "corrupt_checksum": "REJECTED", "trailing_cr": "REJECTED", "history_unchanged": True})
            finally:
                admin.execute(f"DROP DATABASE {db_name} WITH (FORCE)")
                assert admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db_name,)).fetchone() is None
    result = {"status": "PASS", "scope": "LOCALHOST_SYNTHETIC_PG18", "histories": results, "cleanup": "PASS"}
    (out / "history-compatibility.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
