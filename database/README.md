# Bridge School database

PostgreSQL 18 migration package for the School of Sports Bridge.

## Production target

- Managed PostgreSQL: Neon
- Project: `bridge-school-core`
- PostgreSQL: 18
- Neon branch: `production`
- Protected GitHub secret: `NEON_DATABASE_URL`
- Deployment gate branch: `database-production`

## Safety model

1. Ordinary CI never connects to Neon. It uses a disposable PostgreSQL 18 service database inside GitHub Actions.
2. Production migration is triggered only when a tested commit is explicitly promoted to the `database-production` branch. A normal push to `main` cannot modify Neon.
3. Historical migration files are immutable after application. `database/scripts/migrate.sh` records SHA-256 checksums and fails if an applied migration is edited or disappears.
4. A migration must register itself in `schema_migration` before it is considered valid.
5. Production receives only unapplied migrations; already-applied migrations are skipped after checksum verification.
6. Source files in Google Drive are never moved, renamed, deleted, overwritten, or permission-changed by database migration code.
7. Raw/source observations are append-only. Corrections create new observations/events.
8. Partial analysis output remains staging until an explicit publication generation is activated.
9. Student identity is scoped by `(school_id, person_id)` and external identity decisions remain reversible.
10. Runtime services use NOLOGIN capability roles; credentials are provisioned separately and never receive owner rights.
11. Runtime roles receive no persistent-schema CREATE and no DELETE on school data.

## Runtime capability roles

- `bridge_school_reader` — SELECT access across the school schema; no writes.
- `bridge_school_app` — inherits reader and may INSERT/UPDATE only student-facing operational tables.
- `bridge_school_worker` — inherits app and may additionally write ingestion, evidence, analysis, publication and projection state.
- Migration history, metric/policy definitions and other administration/configuration state remain owner-only for writes.
- Guarded event-publication functions are executable only by `bridge_school_worker`.
- These are NOLOGIN roles. Future application login roles will receive only the capability role they need; no database password is stored in this repository.

## Migration order currently deployed / promoted

- `0001_global_registry.sql` — School, Source, Asset, locations, ChangeSet, DomainEvent, outbox, ingestion.
- `0002_learning_core.sql` — Person/Student, SourceIdentity, learning/bridge core, assessments, analysis and projections.
- `0003_event_publication_order.sql` — partition-local replay cursor assigned after publication.
- `0004_integrity_guards.sql` — agreement overlap guard, dependency DAG, atomic outbox publication helper.
- `0005_runtime_roles.sql` — least-privilege reader/app/worker database capabilities and guarded publication functions.

## Development flow

1. Create a new numbered migration; never edit an already-applied migration.
2. Open a pull request to `main`.
3. `database-ci.yml` builds a clean PostgreSQL 18 instance, applies all migrations, runs invariant and permission tests, re-runs migrations for idempotence, and tests checksum tamper detection.
4. Merge only after CI succeeds.
5. Promote that exact tested commit to `database-production`.
6. `database-production.yml` repeats the preflight tests on a disposable PostgreSQL 18 instance and only then applies unapplied migrations to Neon.

## Secrets

No database password or connection string is stored in the repository or printed by the workflows. Production owner access is supplied only through the GitHub Actions secret `NEON_DATABASE_URL`. Runtime login credentials will be separate from the owner connection and will be created only when an application/worker actually needs them.
