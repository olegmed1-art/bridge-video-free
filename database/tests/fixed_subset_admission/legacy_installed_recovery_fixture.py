"""Synthetic localhost PG18 proof for the exact historical 0060 and v2 recovery."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import types

import connector_recovery_fixture as f
import legacy_installed_packet as legacy

SOURCE = "744d068a2cddfb8347411c8e4fb2bd69d19c09ab"


def historical(path):
    return subprocess.check_output(["git", "show", SOURCE + ":" + path], cwd=f.ROOT)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    if not args.execute:
        print("Offline; --execute requires disposable localhost PG18")
        return
    assert args.evidence and not any(os.environ.get(k) for k in
        ("DATABASE_URL", "NEON_API_KEY", "PGSERVICE", "PGHOST", "PGPASSWORD"))
    out = args.evidence.resolve()
    out.mkdir(parents=True, exist_ok=True)
    result = {"status": "FAIL_OR_UNKNOWN", "scope": "LOCALHOST_SYNTHETIC_ONLY",
              "source_commit": SOURCE, "negative_cases": [], "production_executed": False}
    try:
        with tempfile.TemporaryDirectory(prefix="legacy-installed-") as tmp:
            stage = Path(tmp)
            f.prepare(stage, out)
            old_sql = historical("database/migrations/0060_fixed_subset_admission.sql")
            assert hashlib.sha256(old_sql).hexdigest() == legacy.c.LEGACY_0060_SHA256
            (stage / "source/database/migrations/0060_fixed_subset_admission.sql").write_bytes(old_sql)
            # The historical installer embeds its own 11-capability admission
            # check. New 041-043 consumers are tested after the v2 transition.
            (stage / "source/database/tests/041_universal_video_queue.sql").write_bytes(
                historical("database/tests/041_universal_video_queue.sql"))
            old = types.ModuleType("pinned_historical_installer")
            old.__file__ = str(stage / "historical_connector_packet.py")
            exec(compile(historical("database/recovery/fixed_subset/connector_packet.py"),
                         old.__file__, "exec"), old.__dict__)
            old.ROOT = stage
            shutil.copyfile(f.CODE / (legacy.KEY + ".sql"), stage / "recovery" / (legacy.KEY + ".sql"))
            before = f.snapshot()
            f.write(out / "before-install.json", before)
            with f.connect(admin=True) as db:
                db.autocommit = True
                acl_before = f.acl.collect(db)
            f.acl.write_snapshot(out / "acl-before.json", acl_before)
            install = old.install_payload()["sql_statements"]
            receipt = f.execute(install)
            f.write(out / "synthetic-committed-install-receipt.json", receipt)
            installed = f.snapshot()
            assert len([p for p in installed["function_acl_owner"] if p["owner"] == "video_subset_owner"]) == 24
            assert not any(p["signature"].startswith("video_queue.subset_claim_job(") for p in installed["function_acl_owner"])
            for key in ("batch", "job", "job_event", "sequence"):
                assert installed[key] == before[key]
            assert sorted(len(x["historical_job_ids"]) for x in installed["source_reservation"]) == [1, 2]
            kwargs = {"installed_0060_checksum": legacy.c.LEGACY_0060_SHA256}
            verify = legacy.verify_payload(receipt, **kwargs)["sql_statements"]
            result["negative_cases"].append(f.failure_case("legacy-verify-after-grant", verify, out,
                fail_after=verify.index(f.c.TEMP_GRANT)))
            assert f.execute(verify) == "LEGACY_COMMITTED_INSTALL_RECEIPT_MATCHES"
            assert f.snapshot() == installed
            recover = legacy.recover_payload(receipt, **kwargs)["sql_statements"]
            f.write(out / "synthetic-recovery-envelope.json", recover)
            for name, index in (
                ("legacy-recovery-after-grant", recover.index(f.c.TEMP_GRANT)),
                ("legacy-recovery-after-owner-restore", next(i for i, s in enumerate(recover) if f.c.bare(s).startswith("ALTER FUNCTION " + f.r.CLAIM + " OWNER TO"))),
                ("legacy-recovery-after-default-acl", next(i for i, s in enumerate(recover) if f.c.bare(s).startswith("ALTER DEFAULT PRIVILEGES")))):
                result["negative_cases"].append(f.failure_case(name, recover, out, fail_after=index))
            recovered_receipt = f.execute(recover)
            f.write(out / "synthetic-committed-recovery-receipt.json", recovered_receipt)
            recovered = f.snapshot()
            f.write(out / "after-recovery.json", recovered)
            with f.connect(admin=True) as db:
                db.autocommit = True
                acl_after = f.acl.collect(db)
            f.acl.write_snapshot(out / "acl-after.json", acl_after)
            f.acl.assert_restored(acl_before, acl_after,
                allowed_memberships=frozenset({("bridge_school_app", "bridge_school_worker")}))
            assert recovered["legacy"] == before["legacy"]
            for key in ("batch", "job", "job_event", "sequence", "source_reservation",
                        "subset_approval", "source_observation", "subset_admission", "roles", "memberships"):
                assert recovered[key] == installed[key], key
            prior = {r["migration_key"]: r for r in installed["registry"]}
            current = {r["migration_key"]: r for r in recovered["registry"]}
            assert all(current[k] == v for k, v in prior.items()) and set(current) - set(prior) == {legacy.KEY}
            env = os.environ.copy()
            env.update(DATABASE_URL=f.OWNER, MIGRATIONS_DIR=(stage / "source/database/migrations").as_posix(),
                       EXTERNAL_HISTORY_FILE=(f.ROOT / "database/external_migration_history.tsv").as_posix())
            # Keep the historical installer source outside ordinary pending files.
            (stage / "source/database/migrations/0060_fixed_subset_admission.sql").unlink()
            migration = f.command(["bash", "database/scripts/migrate.sh"], out / "migrator.log", env=env)
            assert "[legacy-744d068a]" in migration.stdout and "[legacy-installed-v2]" in migration.stdout
            assert "Applying migration" not in migration.stdout
            assert f.snapshot() == recovered
            for number in ("041", "042", "043"):
                sql = next((f.ROOT / "database/tests").glob(number + "_*.sql"))
                f.psql(sql, out / (number + "-recovered.log"))
            # Behavior regressions can advance a synthetic sequence. Recovery's
            # own preservation was already proved before running those tests.
            marker = next(r for r in recovered["registry"] if r["migration_key"] == legacy.KEY)
            for case, mutation, message in (
                ("legacy-bad-marker", f"UPDATE schema_migration SET checksum=repeat('0',64) WHERE migration_key='{legacy.KEY}'", "LEGACY_RECOVERY_STATE_INVALID"),
                ("legacy-missing-marker", f"DELETE FROM schema_migration WHERE migration_key='{legacy.KEY}'", "SUBSET_EXACT_FUNCTION_ACL"),
                ("legacy-api-enabled", "GRANT EXECUTE ON FUNCTION video_queue.subset_disabled() TO synthetic_unlisted", "LEGACY_RECOVERY_STATE_INVALID"),
                ("legacy-dual-marker", f"INSERT INTO schema_migration(migration_key,checksum) VALUES('{f.r.KEY}','7202fb09ed4a796d4dc6b9d80905b620ede18df4ce38254f133b6a53a0a879e5')", "LEGACY_RECOVERY_STATE_INVALID")):
                for number in ("041", "042", "043"):
                    original = f.snapshot()
                    f.execute([mutation], admin=True)
                    try:
                        sql = next((f.ROOT / "database/tests").glob(number + "_*.sql"))
                        failed = f.psql(sql, out / (case + "-" + number + ".log"), expected=False)
                        assert message in failed.stderr, failed.stderr
                    finally:
                        with f.connect(admin=True) as db:
                            db.execute("INSERT INTO schema_migration(migration_key,applied_at,checksum) VALUES(%s,%s,%s) ON CONFLICT(migration_key) DO UPDATE SET applied_at=excluded.applied_at,checksum=excluded.checksum",
                                (marker["migration_key"], marker["applied_at"], marker["checksum"]))
                            db.execute("DELETE FROM schema_migration WHERE migration_key=%s", (f.r.KEY,))
                            db.execute("REVOKE EXECUTE ON FUNCTION video_queue.subset_disabled() FROM synthetic_unlisted")
                    assert f.snapshot() == original
                    result["negative_cases"].append({"name": case + "-" + number,
                        "status": "PASS", "exact_snapshot_equal": True})
            result.update(status="PASS_LEGACY_INSTALLED_RECOVERY_V2", retained_rows=True,
                duplicate_reservations=True, legacy_acl_owner_definition_equal=True,
                original_registry_unchanged=True, retained_tables=4, retained_roles=5,
                consumers={n: "PASS" for n in ("041", "042", "043")},
                repair_sha256=legacy.BODY_SHA256, installed_0060_checksum=legacy.c.LEGACY_0060_SHA256)
    finally:
        f.write(out / "results.json", result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
