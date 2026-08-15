# Физическая архитектура данных — фактическая реализация v0.2

Дата фиксации: 2026-08-15

## Production database

- Neon project: `bridge-school-core`
- Region: AWS Europe Central 1 (Frankfurt)
- PostgreSQL: 18.4
- Production schema_migration: 0001–0019
- Для всех зарегистрированных migration записан checksum.

Миграции после предыдущей фиксации v0.1:

- 0016_runtime_principals
- 0017_query_observability
- 0018_runtime_function_acl_hardening
- 0019_default_function_acl_fix

Документ, фиксировавший 0001–0015, остается исторической контрольной точкой и не переписывается.

## Runtime access

Capability roles остаются NOLOGIN:

- bridge_school_reader
- bridge_school_app
- bridge_school_worker
- bridge_school_health

Production principals на момент проверки имеют LOGIN:

- bridge_school_app_principal
- bridge_school_worker_principal
- bridge_school_health_principal

Секреты не хранятся в migration files или документации.

## Operational health

На момент проверки production health summary:

- overall_severity = ok
- critical = 0
- warning = 0
- ok = 15

## Фактические данные

На момент проверки:

- Person = 0
- Student = 0
- Source = 4
- KnowledgeItem = 0
- LearningInteraction = 0
- Tournament = 0
- Artifact = 10
- MediaAsset = 4
- Transcript = 10

Следствие: инфраструктура уже используется для media/artifacts/transcripts, но реальные члены/ученики и машинный канон еще не загружены.

## Backend/API

FastAPI service существует в `bridge_school_api` и использует отдельный app principal. Реализованы базовые health/read endpoints; это еще не Member API.

Текущая защита `/v1/` использует общий `BRIDGE_API_TOKEN`; перед внешним многопользовательским доступом требуется персональная AuthIdentity и object-level authorization.

## Vercel

Существует project `bridge-video-free`, связанный с GitHub repository `olegmed1-art/bridge-video-free`. Production deployment из `main` на момент проверки READY; в проверенном последнем часе runtime errors отсутствовали.

Видеоалгоритм и School API пока находятся в одном deploy project. До публичного Club UI deployment boundaries должны быть разделены логически или физически.

## Google Drive

Создана область `Управление клубом` с подпапками:

1. Регламенты и документы клуба
2. Шаблоны и формы
3. Документы членов клуба
4. Финансовые документы
5. Коммуникации
6. Клубные мероприятия
7. Отчёты и выгрузки
90. Архив

Эта область — файловый слой, не база членов клуба.

## Следующие шаги

1. Club Operations migration 0020+ с тестами.
2. AuthIdentity/object authorization отдельным слоем.
3. Controlled import первых Person/Student/ClubMember.
4. Knowledge/Canon ingestion + visibility.
5. Member API + Club Window.
6. Communication Hub + Admin UI.
7. Backup/restore, privacy/security, load gates перед реальными финансовыми и массовыми пользовательскими данными.
