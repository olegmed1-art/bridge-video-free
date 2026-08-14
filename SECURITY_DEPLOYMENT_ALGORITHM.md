# Алгоритм безопасного развёртывания и аудита

Версия: 2.4
Дата: 2026-08-14

Этот алгоритм применяется к инфраструктуре Bridge School: GitHub Actions, Neon PostgreSQL и Vercel.

## Перед изменением

1. Проверить текущие состояния `main`, `database-production`, production Neon и Vercel.
2. Проверить реестр миграций, отсутствие пропущенных checksum и `operational_health_summary`.
3. Проверить последние runtime errors отдельно за короткое актуальное окно и за расширенный период, чтобы не смешивать старые устранённые сбои с текущими.
4. Определить, какие workflows могут запуститься от изменения. Нерелевантные jobs не должны стартовать побочным эффектом.
5. Для изменений `vercel.json` и `.vercelignore` обязательно дождаться реального Preview deployment и проверить его build log до merge.
6. Перед изменением runtime credential contract проверить, что текущий секрет уже соответствует новому контракту отдельным smoke-тестом либо подготовить ротацию до merge.

## Изменение базы

1. Уже применённая миграция не редактируется. Исправление выполняется только следующей forward migration.
2. Новая миграция обязана зарегистрировать себя в `schema_migration`.
3. Исторические checksum проверяются до применения новой миграции.
4. Параллельные миграции сериализуются advisory lock.
5. Изменение прав доступа сопровождается исполняемой проверкой или regression test.
6. Вновь создаваемые owner-функции закрыты для `PUBLIC` по умолчанию. Runtime-функция получает `EXECUTE` только явным grant нужной capability-role.
7. Для PostgreSQL default function privileges используется глобальное правило owner-role; schema-scoped revoke не считается достаточной защитой.
8. `SECURITY DEFINER` функции должны иметь фиксированный безопасный `search_path` и не иметь общего runtime-доступа без явного обоснования.
9. Неучтённые пользовательские функции в production считаются schema drift и должны быть либо внесены в миграции, либо удалены новой миграцией после проверки зависимостей.
10. Runtime principals не получают `SUPERUSER`, `CREATEROLE`, `CREATEDB`, `REPLICATION`, `BYPASSRLS` или `CREATE` в рабочей схеме.
11. Опасные табличные привилегии (`DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`) проверяются отдельно для runtime capability-roles и principals.

## Runtime-доступ к базе

1. App, worker и health используют разные dedicated principals и разные секреты.
2. Runtime-процесс не использует owner connection string.
3. Preflight проверяет фактического `current_user`, membership и effective privileges до полезной работы.
4. Если компонент должен сохранять результат в Neon и его database credential настроен как обязательный, ошибка preflight является ошибкой всего задания. Запрещено незаметно продолжать работу и выдавать успешный результат без database persistence.
5. Отказоустойчивый режим без базы допустим только если он явно объявлен отдельным режимом продукта и результат помечается как неполный; скрытый fallback запрещён.
6. Пароль, DSN или токен никогда не выводятся в лог. Диагностика ошибок подключения должна быть санитизирована.
7. Все production runtime DSN хранятся только как полный PostgreSQL URI. Password-only, `.env`-фрагменты и автоматическая сборка URI из захардкоженного host запрещены для app, worker и health.
8. Каждый DSN-contract проверяет dedicated principal, database, ожидаемый production endpoint, допустимый порт, TLS и channel binding до полезной работы.
9. App и worker используют pooled Neon endpoint. Health может использовать direct или pooled production endpoint, поскольку выполняет короткие read-only проверки; оба варианта должны быть явно ограничены списком production hosts.
10. После каждой ротации runtime database credential запускается dedicated smoke-test. Для worker он обязан проверить полный путь persistence с rollback.
11. Изменение любого DSN-parser сопровождается отрицательными regression tests: неверный principal, branch/host, database, TLS, channel binding, отсутствие пароля, password-only и `.env`-assignment должны отклоняться.

## GitHub Actions

1. Каждый workflow имеет минимальные `permissions`.
2. Push-trigger ограничивается одновременно branch allowlist и path allowlist.
3. Отсутствие ожидаемого request-файла означает fail-closed; запрещён fallback на старое задание.
4. Сторонние Actions фиксируются по immutable commit SHA.
5. Checkout выполняется без сохранения git credentials, если последующим шагам не нужен push.
6. Зависимости устанавливаются до выдачи cloud identity и долгоживущих runtime credentials.
7. Runtime credential передаётся только тому step, которому он нужен, а не всему job без необходимости.
8. Production owner DSN передаётся только шагам проверки/миграции/верификации; checkout и установка пакетов его не получают.
9. Пользовательский ввод сначала передаётся через environment и валидируется до использования. Прямая подстановка выражений GitHub в shell-команду запрещена, если значение может содержать пользовательские данные.
10. Пользовательские медиа и расшифровки не публикуются как GitHub Actions artifacts.
11. Workflow, который способен записывать результат во внешнюю систему, должен завершаться ошибкой при нарушении обязательной persistence boundary; успешное завершение без записи запрещено.
12. Scheduled security/health workflows подчиняются тем же требованиям, что и production workflows: immutable action SHA, `persist-credentials: false`, pinned dependency versions и step-scoped secrets.
13. CI path filters обязаны включать regression tests и workflow-файлы соответствующего runtime boundary, чтобы изменение проверки не могло обойти CI.

## Vercel

1. Production function остаётся в `fra1`, рядом с Neon Frankfurt.
2. Production и Preview не используют один и тот же database credential.
3. Любая оптимизация Ignored Build Step сначала проверяется на Preview. Ошибка ignored-build command считается блокирующей merge.
4. Нельзя предполагать, что `.git` доступен команде `ignoreCommand`: `.vercelignore` может удалить git metadata до её запуска. Git-based ignore rule применяется только после фактической проверки build log.
5. Неудачный эксперимент с Vercel-конфигурацией закрывается без merge; production-конфигурация не ослабляется ради уменьшения числа deployments.
6. После изменения API проверяются `/healthz`, protected endpoint без token, отсутствие OpenAPI docs и отсутствие новых runtime error clusters за актуальное короткое окно.

## CI gate

До merge должны пройти все проверки, относящиеся к изменению: PostgreSQL 18 clean migration, invariants/permissions, idempotence, checksum tamper guard, Python 3.12 compile/import/contract tests и сборка runtime, где она применима. Failed CI не обходится ручным production-изменением. Изменение credential contract без regression test считается неполным. DSN regression tests должны выполняться в общем Database CI, а не только в scheduled runtime workflow.

## Production promotion

1. Сначала merge протестированного изменения в `main`.
2. Изменения БД продвигаются через `database-production`.
3. Production workflow сначала повторяет PostgreSQL 18 preflight на disposable database.
4. Только после успешного preflight применяются unapplied immutable migrations к Neon production.
5. Owner-доступ к production не используется приложением, worker или health monitor.

## После развёртывания

Обязательно проверить:

- ожидаемую последнюю миграцию и `checksum missing = 0`;
- `critical = 0` в operational health;
- фактические effective privileges runtime principals;
- отсутствие `SUPERUSER`, `CREATEROLE`, `CREATEDB`, `REPLICATION`, `BYPASSRLS` у runtime principals;
- отсутствие `CREATE` в рабочей схеме у runtime principals;
- отсутствие опасных табличных привилегий, которые не требуются компоненту;
- отсутствие неожиданного `PUBLIC EXECUTE` у пользовательских owner-функций;
- `/healthz` = 200;
- защищённый endpoint без bearer token = 401;
- отключённые API docs = 404;
- отсутствие новых необъяснённых Vercel runtime errors;
- список реально запустившихся GitHub workflows: database promotion не должен запускать обработку видео;
- worker rollback-smoke: успешное подключение и полный путь persistence без фактического изменения production-данных;
- health monitor: строгий DSN-contract, least-privilege read и актуальный operational health должны завершиться PASS.

## Правило реакции на найденную ошибку

Не ослаблять проверку ради зелёного статуса. Зафиксировать root cause, сделать forward fix, добавить regression test, повторить CI и post-deploy verification. Если дефект показал недостаток процесса, это правило добавляется в следующую версию алгоритма.

## Изменения версии 2.4

Четвёртая волна аудита устраняет последний permissive production DSN-parser у health monitor. Health credential теперь должен быть полным URI с точным dedicated principal, production host, database, TLS и channel binding; password-only и `.env` fallback запрещены. Scheduled health workflow получает immutable action pins, pinned `psycopg`, `persist-credentials: false` и step-scoped secret. DSN regression test health boundary включён в общий Database CI.

## Изменения версии 2.3

Добавлены правила после третьей волны глубокого аудита: обязательный smoke после ротации runtime credentials, запрет password-only и `.env` fallback для production DSN, проверка production endpoint/TLS/channel binding, явная проверка опасных атрибутов ролей и табличных привилегий, а также разделение старых и текущих Vercel runtime errors по временному окну. Worker DSN-contract переведён на fail-closed полный pooled production URI.

## Изменения версии 2.2

Версия 2.2 добавляет правила из второй волны глубокого аудита 2026-08-13: database persistence worker становится fail-closed, production owner DSN переводится на step scope, а изменения Vercel ignored-build считаются безопасными только после успешного реального Preview build. Отдельно зафиксирован обнаруженный случай, когда `.vercelignore` удалил `.git` до `ignoreCommand` и сделал документированный `git diff`-подход неработоспособным в конкретной конфигурации проекта.

## Изменения версии 2.1

Добавлены правила после глубокого аудита 2026-08-13: проверка реальных workflow runs после promotion, запрет stale fallback job id, step-scoped credentials, установка зависимостей до cloud authentication, защита от удалённого имени файла как локального пути, контроль schema drift функций и отдельная проверка глобальных default privileges PostgreSQL.
