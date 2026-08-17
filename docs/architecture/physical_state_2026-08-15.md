# Физическая архитектура данных — фактическая реализация v0.2

Дата фиксации: 2026-08-15; актуализация после восьмой проверки: 2026-08-17

## Production database

- Neon project: `bridge-school-core`
- Region: AWS Europe Central 1 (Frankfurt)
- PostgreSQL: 18.4
- Production schema_migration: `0001–0019`
- Для всех зарегистрированных production migration записан checksum.
- Последняя прямая повторная проверка 2026-08-17 подтверждает: Club Operations/Auth `0020–0043` в production не применены.
- `club_membership`, `auth_identity`, `actor_context_signing_secret` в production отсутствуют.

Миграции production после исторической фиксации v0.1:

- 0016_runtime_principals
- 0017_query_observability
- 0018_runtime_function_acl_hardening
- 0019_default_function_acl_fix

Документ, фиксировавший 0001–0015, остается исторической контрольной точкой и не переписывается.

## Runtime access

Production capability roles остаются NOLOGIN и без superuser/createdb/createrole/replication/bypassrls:

- bridge_school_reader
- bridge_school_app
- bridge_school_worker
- bridge_school_health

Production principals имеют LOGIN, но не административные атрибуты:

- bridge_school_app_principal
- bridge_school_worker_principal
- bridge_school_health_principal

Candidate `main` дополнительно определяет, но production пока не содержит:

- `bridge_school_member` — узкая member-facing capability без наследования broad reader;
- `bridge_school_member_principal` — будущий server-side principal, намеренно NOLOGIN;
- `bridge_school_auth_gateway` — NOLOGIN capability для установки actor context только после внешней аутентификации.

Секреты production runtime не хранятся в migration files или документации. Candidate actor-context signing secret создается внутри БД и недоступен runtime-ролям; в production этой таблицы пока нет.

## Operational health

Последняя production проверка 2026-08-17:

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

Real AuthIdentity/ClubMember import также не выполнялся.

Media/transcript данные относятся к существующему media-контуру. Машинный канон знаний еще не загружен.

## Backend/API

FastAPI service существует в `bridge_school_api` и использует отдельный app principal. Реализованы базовые health/read endpoints; это еще не Member API.

Текущая защита `/v1/` по-прежнему использует общий `BRIDGE_API_TOKEN`. Round-8 database candidate добавил AuthIdentity/object-isolation/actor-context primitives, но они еще не подключены к production API и не являются сами по себе проверкой внешнего OAuth/phone/provider token.

До внешнего многопользовательского доступа нужен server-side authentication gateway, который сначала проверяет внешний токен/сессию, затем устанавливает подписанный transaction-local actor context. Клиент не должен получать database credentials.

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

## Candidate Club Operations/Auth на GitHub main

Кандидат состоит из миграций `0020–0043`.

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
- PaymentAllocation chronology relative to both Payment and Charge;
- provider-neutral AuthIdentity mapping to canonical Person;
- school/context role assignment plus explicit person-to-person grants;
- fail-closed member self-service projections without direct broad base-table reads;
- signed transaction/backend-bound actor context resistant to forged custom PostgreSQL settings;
- protected actor/request audit history for sensitive operations;
- separate trusted auth-gateway capability; ordinary member capability cannot select an arbitrary identity context;
- school-wide role helper cannot silently promote a scoped group/course role;
- member SECURITY DEFINER function surface is regression-whitelisted.

Database tests `011–030` cover positive and adversarial Club Operations/Auth scenarios in addition to all legacy tests.

Round-8 core auth post-merge verification: GitHub Actions run `32000018735`, `success`.
Trusted-gateway candidate verification: run `32000241026`, `success`.
Latest post-merge candidate verification: GitHub Actions run `32000346201`, PostgreSQL 18 — clean migration install, runtime DSN regression, all invariant/adversarial tests, idempotence, checksum tamper guard and migration registry verification all `success`.

## Authentication/authorization boundary — current factual state

Implemented on candidate `main`:

- identity mapping and validity;
- school/context role assignments;
- explicit cross-person permission grants;
- narrow member read views;
- signed transaction-local actor context;
- actor-aware sensitive-operation audit;
- trusted database auth-gateway capability boundary.

Not implemented/deployed yet:

- verification of a real external provider token/session by the production API;
- member server LOGIN credential provisioning;
- guarded member/admin write API;
- instructor-specific object-scoped educational views/functions;
- real AuthIdentity records;
- end-to-end browser/phone login flow.

Therefore AuthIdentity/object isolation is a verified database candidate, not a production member login system yet.

## Production release boundary

Production promotion is currently intentionally blocked.

- GitHub branch `database-production` is unprotected.
- The workflow currently installed on that branch still auto-runs on qualifying pushes and can apply migrations to Neon.
- A hardened manual workflow exists on `main`, but has not been installed on the actual production branch.
- `main` and `database-production` are heavily diverged; an indiscriminate merge is prohibited by the current release plan.

Before promotion: create a recoverable Neon checkpoint, deliberately harden the production release path, verify production still reports `0019`, promote only reviewed database/release files, dispatch migration manually, and verify registry/permissions/health afterward.

## Следующие архитектурные этапы

1. External authentication verifier/gateway integration; database credentials remain server-side.
2. Guarded member/admin write operations and explicit instructor object scopes.
3. Controlled pilot import Person/Student/ClubMember/AuthIdentity with reconciliation.
4. Knowledge/Canon ingestion + visibility.
5. Member API + Club Window only after end-to-end authorization gates.
6. Communication adapters/Admin UI after channel policies are approved.
7. Backup/restore, privacy/retention, security and load/cost gates before real financial and mass-user rollout.

Не утвержденные правила не внедряются автоматически: `ContactPreference=unknown`, event capacity/waitlist, discount/override pricing and recurring subscription semantics remain explicit policy decisions.
