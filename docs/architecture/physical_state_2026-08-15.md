# Физическая архитектура данных — фактическая реализация v0.2

Дата фиксации: 2026-08-15; актуализация после седьмой проверки: 2026-08-16

## Production database

- Neon project: `bridge-school-core`
- Region: AWS Europe Central 1 (Frankfurt)
- PostgreSQL: 18.x
- Production schema_migration: `0001–0019`
- Для всех зарегистрированных production migration записан checksum.
- Последняя прямая повторная проверка подтверждает: Club Operations `0020–0037` в production не применены.
- `club_membership`, `club_payment_refund`, `person_package_grant` в production отсутствуют.

Миграции production после исторической фиксации v0.1:

- 0016_runtime_principals
- 0017_query_observability
- 0018_runtime_function_acl_hardening
- 0019_default_function_acl_fix

Документ, фиксировавший 0001–0015, остается исторической контрольной точкой и не переписывается.

## Runtime access

Capability roles остаются NOLOGIN и без superuser/createdb/createrole/replication/bypassrls:

- bridge_school_reader
- bridge_school_app
- bridge_school_worker
- bridge_school_health

Production principals имеют LOGIN, но не административные атрибуты:

- bridge_school_app_principal
- bridge_school_worker_principal
- bridge_school_health_principal

Секреты не хранятся в migration files или документации.

## Operational health

Последняя production проверка:

- overall_severity = ok
- critical = 0
- warning = 0
- ok = 15

## Фактические данные

Реальный реестр членов/учеников в production еще не импортирован. Ранее подтверждено:

- Person = 0
- Student = 0
- Source = 4
- KnowledgeItem = 0
- Artifact = 10

Media/transcript данные относятся к существующему media-контуру. Машинный канон знаний еще не загружен.

## Backend/API

FastAPI service существует в `bridge_school_api` и использует отдельный app principal. Реализованы базовые health/read endpoints; это еще не Member API.

Текущая защита `/v1/` использует общий `BRIDGE_API_TOKEN`; перед внешним многопользовательским доступом требуется персональная AuthIdentity и object-level authorization/RLS или эквивалентная fail-closed изоляция.

## Google Drive

Существует область `Управление клубом` с подпапками:

1. Регламенты и документы клуба
2. Шаблоны и формы
3. Документы членов клуба
4. Финансовые документы
5. Коммуникации
6. Клубные мероприятия
7. Отчёты и выгрузки
90. Архив

Эта область — файловый слой, не база членов клуба.

## Candidate Club Operations на GitHub main

Кандидат состоит из миграций `0020–0037`.

Основные реализованные контуры:

- membership/contact/service/package/event/booking core;
- financial ledger, payments/allocations/adjustments and dedicated finance capability;
- communications/campaigns/admin tasks;
- deterministic state ordering and runtime permission hardening;
- entitlement and delivery integrity;
- append-only allocation corrections;
- person-specific package acquisitions and package prices;
- immutable commercial version history;
- append-only cash refunds and refund-aware balances;
- acquired-package snapshot protection;
- append-only ClubMembership lifecycle history;
- communication/campaign/admin identity hardening;
- commercial provenance validation at charge/grant time;
- historical entitlement usage and delivery validation by their original business-time validity windows;
- explicit lifecycle closure boundaries and protection from retroactive shortening that would invalidate recorded facts;
- acquired-package validity enforced for package-backed entitlement usage;
- unambiguous Charge commercial origin and Booking/Service provenance consistency;
- PaymentAllocation chronology relative to both Payment and Charge.

Database tests `011–023` cover positive and adversarial Club Operations scenarios in addition to all legacy tests.

Seventh-pass candidate verification: GitHub Actions run `31910012398`, `success`.
Latest post-merge candidate verification: GitHub Actions run `31910114003`, PostgreSQL 18 — clean migration install, runtime DSN regression, all invariant tests, idempotence, checksum tamper guard and migration registry verification all `success`.

## Production release boundary

Production promotion is currently intentionally blocked.

- GitHub branch `database-production` is unprotected.
- The workflow currently installed on that branch still auto-runs on qualifying pushes and can apply migrations to Neon.
- A hardened manual workflow exists on `main`, but has not been installed on the actual production branch.
- `main` and `database-production` are heavily diverged; an indiscriminate merge is prohibited by the current release plan.

Before promotion: create a recoverable Neon checkpoint, deliberately harden the production release path, verify production still reports `0019`, promote only reviewed database/release files, dispatch migration manually, and verify registry/permissions/health afterward.

## Следующие архитектурные этапы

1. AuthIdentity + object-level authorization/RLS or equivalent fail-closed isolation.
2. Actor/audit context for sensitive member/admin actions.
3. Controlled pilot import Person/Student/ClubMember with reconciliation.
4. Knowledge/Canon ingestion + visibility.
5. Member API + Club Window only after authorization gates.
6. Communication adapters/Admin UI after channel policies are approved.
7. Backup/restore, privacy/retention, security and load/cost gates before real financial and mass-user rollout.

Не утвержденные правила не внедряются автоматически: `ContactPreference=unknown`, event capacity/waitlist, discount/override pricing and recurring subscription semantics remain explicit policy decisions.
