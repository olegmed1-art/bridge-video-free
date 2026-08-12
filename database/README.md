# Bridge School database

PostgreSQL 18 migration package for the School of Sports Bridge.

## Production target

- Managed PostgreSQL: Neon
- Project: `bridge-school-core`
- PostgreSQL: 18
- Branch: `production`
- Connection secret: `NEON_DATABASE_URL` in GitHub Actions

## Safety rules

1. Source files in Google Drive are never moved, renamed, deleted, overwritten, or permission-changed by database migration code.
2. Raw/source observations are append-only. Corrections create new observations/events.
3. Database migrations use `ON_ERROR_STOP` and explicit transactions.
4. CI tests run inside a transaction and end with `ROLLBACK`; they do not leave test data in production.
5. `event_position` is assigned at outbox publication time, not at initial event insert time.
6. Partial analysis output remains staging until an explicit publication generation is activated.
7. Student identity is scoped by `(school_id, person_id)` and external identities remain reversible.

## Migration order

- `0001_global_registry.sql` — School, Source, Asset, locations, ChangeSet, DomainEvent, outbox, ingestion.
- `0002_learning_core.sql` — Person/Student, SourceIdentity, learning/bridge core, assessments, analysis and projections.
- `0003_event_publication_order.sql` — partition-local replay cursor assigned after commit.
- `0004_integrity_guards.sql` — agreement overlap guard, dependency DAG, atomic outbox publication helper.

## CI

`.github/workflows/database-migrate.yml` connects using the protected GitHub secret, verifies PostgreSQL 18, applies migrations in lexical order, then runs invariant smoke tests.

No database password or connection string is stored in the repository.
