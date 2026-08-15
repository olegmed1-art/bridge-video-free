# Физическая архитектура данных — фактическая реализация v0.2

Дата фиксации: 2026-08-15

## Production database

- Neon project: `bridge-school-core`
- Region: AWS Europe Central 1 (Frankfurt)
- PostgreSQL: 18.4
- Production schema_migration: `0001–0019`
- Для всех зарегистрированных production migration записан checksum.
- Повторная проверка подтверждает: Club Operations `0020–0026` в production еще не применены.

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

Production principals на момент повторной проверки имеют LOGIN, но не административные атрибуты:

- bridge_school_app_principal
- bridge_school_worker_principal
- bridge_school_health_principal

Секреты не хранятся в migration files или документации.

## Operational health

Повторная production проверка:

- overall_severity = ok
- critical = 0
- warning = 0
- ok = 15

## Фактические данные

На момент повторной проверки:

- Person = 0
- Student = 0
- Source = 4
- KnowledgeItem = 0
- Artifact = 10

Ранее зафиксированные media/transcript данные остаются частью существующего media-контура. Реальные члены/ученики и машинный канон еще не загружены.

## Backend/API

FastAPI service существует в `bridge_school_api` и использует отдельный app principal. Реализованы базовые health/read endpoints; это еще не Member API.

Текущая защита `/v1/` использует общий `BRIDGE_API_TOKEN`; перед внешним многопользовательским доступом требуется персональная AuthIdentity и object-level authorization.

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

Кандидат теперь состоит из миграций `0020–0026`:

- 0020 — membership, contacts/preferences, services/prices, packages/entitlements, events/bookings;
- 0021 — financial ledger и finance capability;
- 0022 — communications/campaigns/admin tasks;
- 0023 — deterministic state ordering;
- 0024 — runtime/financial hardening;
- 0025 — entitlement/financial/delivery integrity hardening;
- 0026 — semantic integrity: commercial-period overlap, package entitlement rules, price/service consistency, active-contact routing and ClubEvent specialization.

Тесты `011–013` покрывают основной сценарий и негативные integrity/security cases.

Последняя проверка кандидата: GitHub Actions run `31905952353`, PostgreSQL 18; clean migration install, all invariant tests, idempotence, checksum tamper guard и registry verification — `success`.

## Исправления, найденные повторной проверкой

1. Entitlement нельзя расходовать сверх выданного количества; его реверс должен точно относиться к исходной операции того же Entitlement.
2. Новый расход требует активного Entitlement, но корректирующий реверс не блокируется после завершения/отзыва.
3. Entitlement из package обязан соответствовать package_service_rule и не превышать количество правила.
4. Общий interactive app больше не может сам выдавать Entitlement; выдача вынесена в доверенный finance/admin capability.
5. `person_financial_balance` теперь отражает все полученные Payments, включая еще не распределенные; `person_allocated_receivable_balance` отдельно показывает сверку начислений по allocations.
6. Реверс financial adjustment должен точно компенсировать исходную сумму.
7. PriceVersion в Charge должен принадлежать той же Service.
8. Активные PriceVersion/PackageVersion не могут иметь неоднозначно перекрывающиеся периоды.
9. Для Person/channel допускается один предпочтительный активный контакт.
10. Новая доставка не использует отозванный контакт, несовместимый канал или явный ContactPreference=`denied`; delivery timestamps проверяются на последовательность.
11. ClubEvent не может одновременно ссылаться и на Session, и на Tournament.

Политика для `ContactPreference=unknown` намеренно не выдумана: это отдельное бизнес/юридическое правило клуба.

## Следующие шаги

1. Выполнить отдельный controlled production promotion `0020–0026` через существующий `database-production` workflow и затем проверить фактическую production schema/permissions/health.
2. Добавить AuthIdentity и object-level authorization.
3. Выполнить controlled pilot import первых Person/Student/ClubMember с reconciliation.
4. Выполнить Knowledge/Canon ingestion + visibility.
5. Открывать Member API + Club Window только после authorization gates.
6. Подключить Communication Hub/Admin UI.
7. Выполнить backup/restore, privacy/security и load gates перед реальными финансовыми и массовыми пользовательскими данными.
