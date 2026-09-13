# Диана 2 — migration 0049 production read-back

Дата: 2026-08-18
Статус: `PASS`
Предыдущая контрольная точка `DIANA_2_PREFLIGHT_20260818.md` со статусом HOLD сохраняется как история и superseded этим read-back.

## Production identity

- Neon project: `misty-poetry-18012774` (`bridge-school-core`).
- Production branch: `br-wispy-lab-b1rq54of` (`production`).
- Database: `neondb`.
- PostgreSQL server version number: `180004`.

## Recovery

До production-изменения создана recovery-ветка:

- name: `recovery-before-0049-20260818`;
- branch ID: `br-noisy-term-b1wou3tv`;
- parent: production.

Точный SQL миграции был сначала применён и повторно применён на recovery-ветке. Оба запуска завершились успешно; migration registry остался с одной записью, схема и индексы не дублировались.

## Applied migration

- repository file: `database/migrations/0049_analysis_candidate_staging.sql`;
- migration key: `0049_analysis_candidate_staging`;
- exact file SHA-256: `939a2d8948350eedd518b4a3a0ae8d370f35f9bf626b5fcea6f6fe9afe387414`;
- production `applied_at`: `2026-08-18T09:35:07.605Z`;
- migration registry checksum: exact SHA-256 above.

The direct transaction was required because the natural-language Neon migration helper failed before SQL execution while parsing a quoted COMMENT. Direct execution used the exact reviewed repository migration semantics, without changing the migration file.

## Schema read-back

`public.analysis_candidate` exists and contains:

- 17 columns;
- primary key plus foreign keys to school, analysis run, source and superseded candidate;
- JSON object/array integrity checks;
- status and promotion-status checks;
- 5 indexes including the primary-key index, deterministic identity unique index, run/type index, review queue partial index and stable-key index;
- table and column comments describing staging-only authority.

Current candidate row count after migration: `0`.

## Runtime privileges

- `bridge_school_reader`: SELECT = true; INSERT = false.
- `bridge_school_app`: INSERT = false.
- `bridge_school_worker`: SELECT = true; INSERT = true; UPDATE = true; DELETE = false.

The new migration granted no write permission from the staging table into canon, curriculum or production profiles. The application quality-v2 persistence adapter writes only `analysis_candidate`; all promotion remains a separate guarded process.

## Authority and data read-back

Counts before and after migration are unchanged:

- `canon_activation`: 0;
- `course_version`: 1;
- `student_profile_snapshot`: 1;
- `knowledge_item`: 0;
- `knowledge_version`: 0.

No candidate rows, canon activations, course versions, knowledge versions or student-profile rows were created by the migration.

## Idempotence

The exact migration transaction was run a second time on production. Final state:

- migration registry rows for 0049: 1;
- columns: 17;
- indexes: 5;
- candidate rows: 0;
- authoritative counts unchanged.

## Launch gate

- `PRODUCTION_STAGING_READY = YES`
- `MIGRATION_0049_APPLIED = YES`
- `READ_BACK = PASS`
- `IDEMPOTENCE = PASS`
- `RECOVERY_READY = YES`
- `DIANA_2_FULL_LOOP_READY = YES`

Heavy processing of `Диана 2` was not started by this migration operation.