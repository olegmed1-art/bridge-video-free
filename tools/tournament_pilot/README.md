# Две заявки после 1NT: ограниченный учебный пилот

Только согласованные значения 3H/3S, решение TDEC-20261003-002. Два source_rule
объекта побайтно по JSON-значениям совпадают с принятым snapshot на 6f6001c0.
Полный реестр, private IDs, ключи и новые авторские условия не включены.

## Экран уже можно использовать локально

Из корня проекта: `python -m tools.tournament_pilot.screen`, затем открыть
http://127.0.0.1:8765 . Стандартная библиотека + существующие runtime dependencies.
3♥ подходит распределению 3–1–4–5; 3♠ не подходит. Это проверка значения, не выбор
лучшей заявки. Режим явно отмечен как локальный; БД, credentials и сеть не нужны.
`--revoked` показывает отсутствие активного правила. Завершение: Ctrl+C.

После отдельно разрешённого production rollout: тот же экран с
`--live-position <approved synthetic UUID>` и уже существующим BRIDGE_API_TOKEN
в окружении локального процесса. Не вставлять ключ в командную строку, HTML или
журналы. Browser получает только CSRF nonce и минимальное объяснение. Live sender
обращается только к existing Vercel origin, не следует redirects; upstream token
не передаётся другому адресу. Новый пользовательский аккаунт/ключ не создаётся.

API разрешает только сохранённую canary position с фиксированными stable_key,
рукой, dealer/seat и source binding. Другие позиции не получают результат.
SQL catalog штатно school/scope-wide; guard ограничивает именно этот consumer.
L1 и SQL gates не изменены. GET экран не добавляется в публичный API: локальная
оболочка находится в том же проекте и исключена существующим .vercelignore.

## Проверки и честные границы

`python -m pytest -q tools/tournament_pilot/test_pilot.py` проверяет pinned payloads,
все560распределений для каждого call, malformed requests, auth, guard, HTTP redirect,
Host/Origin/CSRF и строгую привязку ответа экрана к rule/hash/version/call.
Изолированный workflow также запускает прежние L1/API regression tests.

`python -m tools.tournament_pilot.rehearsal` предназначен ТОЛЬКО для disposable
PostgreSQL на фиксированном loopback55432. Не запускать против production и не
копировать synthetic review/approval. CI выполняет настоящую авторизацию с
одноразовым synthetic token, реальные SQL gates, source binding, локальный экран
и цикл SUPPORTED → ABSTAIN после отзыва → SUPPORTED после повторной активации.
Это доказательство изолированного пути, не live авторизации Олега или deployment.

Локальный Edge QA: desktop1100px, mobile390px без горизонтального overflow,
Tab/Enter, accessible button names, positive/negative/revoked states, zero console
exceptions. Скриншоты и browser report сохраняются локально вне репозитория.

## Минимальный row budget и откат (пока только предложение)

| Строки | Количество | Назначение / откат |
|---|---:|---|
| source | 1 | Ссылка SRC-0096; сохранить историю |
| knowledge_item/version/version_source/rule | 2 каждого =8 | Два кандидата и происхождение; неактивными сохранить при отказе |
| rule_test/rule_test_run | 8+8=16 | По4обязательных типа на правило, внутри все18случаев; сохранить evidence |
| decision_position | 1 | Только согласованная synthetic hand, без queue/search jobs |
| canon_activation/runtime_activation | 4+4=8 | Первая пара каждой таблицы отзывается; вторая действует до исходного24h expiry |
| ingestion_run/event | 1+5=6 | Один журнал: import, actual review, activation, revoke, reactivation; append-only |
| Итого | **40** | Новые строки в существующей школе |

CI дополнительно создаёт одну synthetic school fixture, не входящую в production
budget. Actual review в production означает проверенное существующее согласование
и provenance, не автоматическое присвоение reviewed за прохождение теста.
8 SQL suites сохраняют все18 observed outcomes, включая interference; результат
явно classified synthetic candidate-evaluator evidence.

До первой activation ошибка оставляет правила неактивными. В транзакции ошибка
откатывает её целиком. После activation отзывать только owned runtime UUIDs, затем
owned canon UUIDs; сохранять definitions/tests/source/position/history. Успешная
проверка отзыва создаёт вторую пару с новым valid_from и прежним expiry (без
продления24h). Авария после закрытого run: максимум ещё1run+1event, то есть42строки
вместо40; дальнейшей реактивации нет. Код откатывается на ранее проверенный
deployment отдельно от SQL revoke. Не удалять BEN history и чужие строки.

## Deployment остаётся выключенным

Ветка основана на main1440920, не включает предыдущий36-file эксперимент.
Production runtime diff — только app.py и tournament_teacher.py. Остальные
файлы: две выдержки, локальный экран, tests/rehearsal/review и один isolated CI.
Preview запрещён vercel.json/ignoreCommand; не обходить этот gate. Только одно
будущее согласование минимального merge в main + existing Vercel release +40row
24hpilot+проверка отката. Новых сервисов, env, прав, ключей, оплаты и прочих правил нет.
Перед исполнением: exact SHA review, current target/side-effect/free quota/access
preflight. Нет существующего доступа или появился платёж — stop до записи.
Private exact targets и пользовательский вопрос находятся в локальном review пакете.

Legacy ai_teacher.py остаётся точной копией main. Vercel entrypoint монтирует
обёртку на том же URL/auth, которая делегирует старые запросы прежнему writer.
Изменение ai_teacher.py запустило бы production BEN workflow с secret; этот
побочный эффект исключён выбором отдельного адаптера, workflow не изменяется.
