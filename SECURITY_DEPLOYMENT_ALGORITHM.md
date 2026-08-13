# Алгоритм безопасного развёртывания и аудита

Версия: 2.2
Дата: 2026-08-13

Этот алгоритм применяется к инфраструктуре Bridge School: GitHub Actions, Neon PostgreSQL и Vercel.

## Перед изменением

1. Проверить текущие состояния `main`, `database-production`, production Neon и Vercel.
2. Проверить реестр миграций, отсутствие пропущенных checksum и `operational_health_summary`.
3. Проверить последние runtime errors.
4. Определить, какие workflows могут запуститься от изменения. Нерелевантные jobs не должны стартовать побочным эффектом.
5. Для изменений `vercel.json` и `.vercelignore` обязательно дождаться реального Preview deployment и проверить его build log до merge.

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

## Runtime-доступ к базе

1. App, worker и health используют разные dedicated principals и разные секреты.
2. Runtime-процесс не использует owner connection string.
3. Preflight проверяет фактического `current_user`, membership и effective privileges до полезной работы.
4. Если компонент должен сохранять результат в Neon и его database credential настроен как обязательный, ошибка preflight является ошибкой всего задания. Запрещено незаметно продолжать работу и выдавать успешный результат без database persistence.
5. Отказоустойчивый режим без базы допустим только если он явно объявлен отдельным режимом продукта и результат помечается как неполный; скрытый fallback запрещён.
6. Пароль, DSN или токен никогда не выводятся в лог. Диагностика ошибок подключения должна быть санитизирована.

## GitHub Actions

1. Каждый workflow имеет минимальные `permissions`.
2. Push-trigger ограничивается одновременно branch allowlist и path allowlist.
3. Отсутствие ожидаемого request-файла означает fail-closed; запрещён fallback на старое задание.
4. Сторонние Actions фиксируются по immutable commit SHA.
5. Checkout выполняется без сохранения git credentials, если последующим шагам не нужен push.
6. Зависимости устанавливаются до выдачи cloud identity и долгоживущих runtime credentials.
7. Runtime credential передаётся только тому step, которому он нужен, а не всему job без необходимости.
8. Production owner DSN передаётся только шагам проверки/миграции/верификации; checkout и установка пакетов его не получают.
9. Пользовательский ввод сначала передаётся через environment и валидируется до использования.
10. Пользовательские медиа и расшифровки не публикуются как GitHub Actions artifacts.

## Vercel

1. Production function остаётся в `fra1`, рядом с Neon Frankfurt.
2. Production и Preview не используют один и тот же database credential.
3. Любая оптимизация Ignored Build Step сначала проверяется на Preview. Ошибка ignored-build command считается блокирующей merge.
4. Нельзя предполагать, что `.git` доступен команде `ignoreCommand`: `.vercelignore` может удалить git metadata до её запуска. Git-based ignore rule применяется только после фактической проверки build log.
5. Неудачный эксперимент с Vercel-конфигурацией закрывается без merge; production-конфигурация не ослабляется ради уменьшения числа deployments.

## CI gate

До merge должны пройти все проверки, относящиеся к изменению: PostgreSQL 18 clean migration, invariants/permissions, idempotence, checksum tamper guard, Python 3.12 compile/import/contract tests и сборка runtime, где она применима. Failed CI не обходится ручным production-изменением.

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
- отсутствие неожиданного `PUBLIC EXECUTE` у пользовательских owner-функций;
- `/healthz` = 200;
- защищённый endpoint без bearer token = 401;
- отключённые API docs = 404;
- отсутствие новых необъяснённых Vercel runtime errors;
- список реально запустившихся GitHub workflows: database promotion не должен запускать обработку видео;
- для worker, которому требуется persistence, отдельный rollback-smoke должен подтвердить успешное подключение и запись без фактического изменения production-данных.

## Правило реакции на найденную ошибку

Не ослаблять проверку ради зелёного статуса. Зафиксировать root cause, сделать forward fix, добавить regression test, повторить CI и post-deploy verification. Если дефект показал недостаток процесса, это правило добавляется в следующую версию алгоритма.

## Изменения версии 2.2

Версия 2.2 добавляет правила из второй волны глубокого аудита 2026-08-13: database persistence worker становится fail-closed, production owner DSN переводится на step scope, а изменения Vercel ignored-build считаются безопасными только после успешного реального Preview build. Отдельно зафиксирован обнаруженный случай, когда `.vercelignore` удалил `.git` до `ignoreCommand` и сделал документированный `git diff`-подход неработоспособным в конкретной конфигурации проекта.

## Изменения версии 2.1

Добавлены правила после глубокого аудита 2026-08-13: проверка реальных workflow runs после promotion, запрет stale fallback job id, step-scoped credentials, установка зависимостей до cloud authentication, защита от удалённого имени файла как локального пути, контроль schema drift функций и отдельная проверка глобальных default privileges PostgreSQL.
