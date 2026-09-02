# Физическая архитектура данных — фактическая реализация v0.2

Дата фиксации: 2026-08-15; актуализация после десятой проверки: 2026-08-17

## Production database

- Neon project: `bridge-school-core`
- Region: AWS Europe Central 1 (Frankfurt)
- PostgreSQL: 18.4
- Production schema_migration: `0001–0019`
- Для всех зарегистрированных production migration записан checksum.
- Последняя прямая повторная проверка 2026-08-17 подтверждает: Club Operations/Auth/Truth `0020–0045` в production не применены.
- `club_membership`, `auth_identity`, `actor_context_signing_secret` в production отсутствуют.
- Production Bridge Video `analysis_run`: 11 успешных строк; `algorithm_version_id` пока не заполнен, потому что Truth migration `0045` еще не продвинута в production.
- `storage_verification` в production пока пуст; это также ожидается до `0045`.

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

Последняя production проверка 2026-08-17 после hardening release path:

- overall_severity = ok
- critical = 0
- warning = 0
- ok = 15
- migration_count = 19
- latest_migration = `0019_default_function_acl_fix`
- missing migration checksums = 0

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

## Candidate Club Operations/Auth/Truth на GitHub main

Кандидат состоит из миграций `0020–0045`.

Последовательность после устранения concurrent collision:

- `0044_instructor_education_scope.sql` — instructor/object-scoped education access candidate;
- `0045_truth_storage_provenance.sql` — META Truth Layer provenance/storage candidate.

Truth migration первоначально была временно пронумерована `0044`, но concurrent development занял этот номер instructor-scope migration. Поскольку Truth migration в production не применялась, она была безопасно перенумерована в `0045`, а ее regression test — из `031` в `032`. Migration runner дополнительно переведен в fail-closed режим: повтор одного numeric prefix среди migrations или database tests останавливает процесс до любых миграций.

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
- member SECURITY DEFINER function surface is regression-whitelisted;
- instructor education scope candidate with explicit object/person authorization boundaries;
- canonical `bridge-video-master-analysis` Algorithm identity;
- AlgorithmVersion identity registry through `3.1-free-r25.11` without equating registration with quality promotion;
- fail-closed linkage of Bridge Video AnalysisRun to AlgorithmVersion;
- append-only StorageVerification evidence linked to AssetLocation and the Asset checksum registry.

Database tests `011–032` cover positive and adversarial Club Operations/Auth/Instructor/Truth scenarios in addition to all legacy tests.

Round-8 core auth post-merge verification: GitHub Actions run `32000018735`, `success`.
Trusted-gateway candidate verification: run `32000241026`, `success`.
Auth-gateway post-merge verification: run `32000346201`, PostgreSQL 18, `success`.
Truth-layer semantic candidate verification before renumbering: run `32000454269`, PostgreSQL 18 — clean migration install, all invariant/adversarial tests, idempotence, checksum-tamper guard and migration-registry verification all `success`.
Final `0045`/`032` numbering plus duplicate-sequence guard requires a fresh post-renumber CI pass before release evidence is complete.

### Truth-layer production-snapshot evidence

Truth migration semantics were additionally checked not only on an empty CI database, but also on an isolated Neon branch created from the factual production state. The isolated check was performed before the sequence-only rename; final release identity is `0045_truth_storage_provenance`.

After applying the Truth logic on this copy:

- canonical Bridge Video Algorithm = 1;
- registered Bridge Video AlgorithmVersion = 9;
- historical Bridge Video AnalysisRun = 11;
- linked AnalysisRun = 11;
- unlinked AnalysisRun = 0;
- AssetLocation = 15;
- StorageVerification = 15;
- locations with verification evidence = 15.

Все 15 проверок хранилища имели `availability_status=available` и были привязаны к checksum из Asset registry. Изолированная тестовая ветка после проверки удалена. Production не изменялся.

## Authentication/authorization boundary — current factual state

Implemented on candidate `main`:

- identity mapping and validity;
- school/context role assignments;
- explicit cross-person permission grants;
- narrow member read views;
- signed transaction-local actor context;
- actor-aware sensitive-operation audit;
- trusted database auth-gateway capability boundary;
- instructor education-scope database candidate.

Not implemented/deployed yet:

- verification of a real external provider token/session by the production API;
- member server LOGIN credential provisioning;
- guarded member/admin write API;
- real AuthIdentity records;
- end-to-end browser/phone login flow.

Therefore AuthIdentity/object isolation and instructor scope are database candidates, not a production member login/teacher portal system yet.

## Production release boundary

Production promotion по-прежнему намеренно заблокирована, но один критический риск release path устранен.

Выполнено 2026-08-17:

- на фактическую ветку `database-production` установлен hardened workflow;
- автоматический `push -> migrate Neon` удален;
- production migration теперь возможна только через `workflow_dispatch`;
- workflow требует явное подтверждение `MIGRATE` и проверяет, что dispatch идет именно с `database-production`;
- перед Neon migration выполняется PostgreSQL-18 preflight и полный набор invariant tests;
- production job привязан к GitHub environment `database-production`;
- перед миграцией записывается production fingerprint;
- после миграции проверяются registry checksums и operational health;
- merge hardening workflow не запустил production migration;
- после hardening production повторно проверена и осталась на `0019`, 15/15 health signals `ok`.

Остающиеся ограничения:

- GitHub branch `database-production` все еще `protected=false`; доступный connector не предоставляет безопасной операции изменения branch-protection settings;
- GitHub environment `database-production` до этого отсутствовал, поэтому само упоминание environment в workflow не следует считать самостоятельным approval gate без отдельной настройки protection rules;
- `main` и `database-production` намеренно сильно расходятся; indiscriminate merge по-прежнему запрещен;
- migrations `0020–0045` еще не продвигались в production.

## Recovery / backup boundary

Neon project сейчас имеет `history_retention_seconds=21600`, то есть restore window около 6 часов. Для долговременной защиты этого недостаточно.

Перед будущим production promotion создана отдельная recovery-ветка:

- name: `recovery-prod-0019-20260817`
- branch ID: `br-bitter-term-b1gkg284`
- source: текущая production ветка `br-wispy-lab-b1rq54of`
- verified state before preservation: migration count 19, latest `0019_default_function_acl_fix`, missing checksums 0, health 15/15 `ok`, Bridge Video AnalysisRun 11, AssetLocation 15.

Ветка хранится отдельно от production как дополнительная контрольная точка. Compute у неиспользуемых Neon branches у этого проекта фактически переходит в idle/suspended state; ветка при этом сохраняется.

Это повышает recoverability, но не заменяет полноценную backup policy. На текущем Neon Free v3 проекте snapshot-management action через подключенный инструмент недоступен, а restore window остается около 6 часов.

## Перед production promotion

Обязательная последовательность:

1. Не изменять сохраненную recovery-ветку `recovery-prod-0019-20260817`.
2. Повторно проверить production fingerprint, latest migration=`0019`, checksums и health.
3. Проверить точный reviewed release set; не делать merge всего `main` в `database-production`.
4. По возможности включить GitHub branch protection и environment protection для `database-production` — это требует операции уровня настроек владельца репозитория, которой текущий connector не предоставляет.
5. Продвигать только проверенные database/release files с уникальными sequence identities.
6. Запускать production workflow только вручную с `MIGRATE`.
7. После migration проверить schema_migration, runtime permissions, health, auth boundaries, 11/11 AlgorithmVersion linkage и StorageVerification counts.
8. Не удалять recovery-ветку до завершения повторной проверки и периода наблюдения.
9. При любом отклонении не объявлять компонент OPERATIONAL; остановить promotion и использовать сохраненное состояние для recovery.

## Следующие архитектурные этапы

1. Завершить release/recovery gates и только затем решать вопрос production promotion candidate database stack.
2. External authentication verifier/gateway integration; database credentials remain server-side.
3. Guarded member/admin write operations around the already-designed member/instructor scopes.
4. Controlled pilot import Person/Student/ClubMember/AuthIdentity with reconciliation.
5. Knowledge/Canon ingestion + visibility.
6. Member API + Club Window only after end-to-end authorization gates.
7. Communication adapters/Admin UI after channel policies are approved.
8. Backup/restore, privacy/retention, security and load/cost gates before real financial and mass-user rollout.

Не утвержденные правила не внедряются автоматически: `ContactPreference=unknown`, event capacity/waitlist, discount/override pricing and recurring subscription semantics remain explicit policy decisions.
