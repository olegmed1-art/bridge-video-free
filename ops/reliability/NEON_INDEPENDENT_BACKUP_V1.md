# Neon independent backup and restore v1

Purpose: close issue #526 with observed evidence, not inference.

## Boundary

The workflow creates a consistent PostgreSQL 18 logical dump from the protected Neon production database, encrypts it before any durable upload, removes the plaintext dump, stores only the encrypted payload plus a non-sensitive manifest in GitHub Actions artifact storage, downloads that encrypted artifact in a separate restore job, decrypts it only inside the isolated restore job, restores it to an ephemeral PostgreSQL 18 service, and verifies source/restore counts plus critical recovery tables.

This is independent from Neon storage. It does not modify production, school canon, student data, Oracle routing, or application routing.

## Required secret

`database-production` must contain:

- `NEON_DATABASE_URL` — existing protected production connection string.
- `NEON_BACKUP_PASSPHRASE` — dedicated recovery passphrase, at least 24 characters. It must be stored separately from repository contents and must not be printed to logs.

The workflow fails closed before backup if either secret is missing.

## Retention

- Daily encrypted generation: 35 days.
- First UTC day of each month: additional encrypted generation retained for 90 days.

No raw database dump is uploaded as a GitHub artifact.

## Acceptance

Source table/schema counts, source schema names, restored counts/names and the manifest use the same scope: the existing `pg_catalog`/`information_schema` exclusions plus only `^pg_(toast_)?temp_[0-9]+$`. This excludes PostgreSQL's temporary system namespaces while retaining `pg_toast` and all other names. The manifest records this temporary namespace pattern; source statistics and `pg_dump` still use separate snapshots.

The restored schema-name set must match the source set exactly within this scope. A missing or unexpected permanent schema fails verification even when schema counts are equal. The existing four count checks and two recovery-marker checks also remain mandatory; all mismatches are reported before failure.

Run `38888790367` remains a recorded **FAIL**. Its complete logged inventories differ only by `pg_temp_23` and `pg_toast_temp_23`; applying the corrected scope to the saved lists yields the same nine schemas. This offline schema comparison needs no new production read, but does not rerun backup, checksum, decryption, restore, or the SQL reads and is not a new full restore PASS. The original counts and failure outcome are retained in `tests/fixtures/neon_restore_run_38888790367.json`.

Issue #526 remains open until one real `/recovery neon-backup-restore` execution proves all of the following:

1. backup job succeeds against production Neon;
2. encrypted artifact has a retained SHA-256 and byte size;
3. restore job downloads the independent artifact rather than reusing the source connection;
4. PostgreSQL 18 restore succeeds with `--exit-on-error`;
5. restored table/schema counts equal source counts captured before dump;
6. `assistant_lab.job` and `assistant_lab.research_job` row counts match source;
7. `public.recovery_checkpoint` and `public.recovery_verification` exist in the restored database;
8. raw dump upload remains false;
9. non-sensitive evidence is posted to issue #526.

Only after observed PASS may `ops/reliability/technical-state.yml` move Neon `backup_status` and `restore_status` to `proven`.

## Trigger

Manual bounded acceptance command on issue #526:

`/recovery neon-backup-restore`

A daily schedule is also present, but it is useful only after the dedicated passphrase is configured and the first acceptance run passes.
