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
