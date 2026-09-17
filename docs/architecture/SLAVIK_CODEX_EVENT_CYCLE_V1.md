# Славик: событийный цикл GitHub → Codex Cloud

Статус: production candidate (`delivery_contract_version=3`)

Дата: 2026-09-14

## Совместимость

Контур рассчитан на личный ChatGPT Pro. Он использует доступную в Pro передачу
GitHub issue/PR в Codex через `@codex`, а не Workspace Agent Trigger API.
Workspace Agent access tokens относятся к Business/Enterprise и этому контуру
не нужны.

Официальные контракты:

- https://learn.chatgpt.com/docs/third-party/github
- https://learn.chatgpt.com/docs/cloud
- https://learn.chatgpt.com/docs/pricing

## Рабочий цикл

1. Neon хранит очередь, exact head, epoch, роль, приоритет и состояние.
2. Resident Oracle worker событийно получает `NOTIFY`, резервирует один из
   шести слотов (пять обычных и один P0), получает exact head через строго
   read-only endpoint закреплённого GitHub App broker и создаёт draft dispatch
   PR. Installation token остаётся внутри broker.
3. Единственная webhook automation проверяет dispatch PR и Neon binding, затем
   публикует один owner-authenticated comment в **target PR**, начиная с точной
   строки `@codex execute this task`. Далее идут пустая строка,
   `SLAVIK_CODEX_DISPATCH_V1`, поля задания, execution policy и result contract.
   Явный глагол задаёт выполнение cloud task; обычное PR review не возвращает
   обязательный terminal envelope. Приёмник также принимает старую первую
   строку `@codex` для уже отправленных команд.
   При rollout сначала развернуть и проверить приёмник с поддержкой обеих
   строк, затем переключить publisher. При откате сначала вернуть publisher на
   `@codex`, после этого можно откатывать поддержку новой строки в приёмнике.
4. GitHub создаёт отдельную изолированную Codex Cloud task/chat. Обычный путь
   фиксирует доставку после `eyes` reaction от закреплённого Codex bot
   (`id=199175422`). Если необязательная UI-reaction не появилась, exact-bound
   terminal того же закреплённого bot/app может сам подтвердить доставку только
   пока outbox остаётся `PUBLISHED` и delivery deadline ещё открыт.
5. GitHub Actions повторно проверяет открытый target PR, его номер и exact
   head, после чего узкий `SECURITY DEFINER` RPC переводит outbox в `SENT`.
   Target PR может опираться на промежуточную ветку; требование base `main`
   применяется только к dispatch PR, проверяемому событийным мостом.
6. Codex завершает ответ блоком `AUTOPILOT_CODEX_RESULT_V1`. Второй callback
   принимает только comment закреплённого GitHub App (`id=1144995`), повторно
   сверяет live head и требует либо сохранённый ACK в открытом callback window,
   либо строго ограниченный `PUBLISHED`/delivery-deadline контракт из шага 4.
   Точная неизменённая provider-фраза `Codex couldn't complete this request.
   Try again later.` принимается как `BLOCKED` только от того же закреплённого
   bot/app, не позднее пяти минут и только когда она является следующим
   issue-comment после валидной owner-команды на том же PR. Перед ingestion оба
   комментария повторно считываются; любой intervening/edited/ambiguous случай
   остаётся fail-closed до обычного deadline reconciliation.
7. Neon атомарно сохраняет terminal receipt/evidence, закрывает task/work item
   и посылает `NOTIFY` для следующей независимой задачи.

## Инварианты

- `PUBLISHED` означает только существование dispatch PR, не доставку.
- Один `dispatch_id` получает не более одного ACK и одного terminal receipt;
  повтор точного payload идемпотентен, конфликтующий повтор отклоняется.
- Terminal без `eyes` не создаёт синтетический ACK: `codex_ack_*` остаются NULL,
  а retained evidence явно помечается `PINNED_CODEX_TERMINAL`.
- Нет OpenAI API key, Workspace Agent token или сохранённой ChatGPT-сессии на
  Oracle.
- Нет анонимного GitHub API polling: PR-head probe использует отдельный
  least-privilege token с единственным permission `pull_requests:read`.
- Нет polling для нормального task-to-task перехода; deadline reconciliation
  остаётся только аварийной страховкой.
- READ_ONLY/VERIFY не меняют код. REPAIR разрешён только роли с
  `execution_scope=REPOSITORY` и `can_repair=true`; merge, deploy, production,
  Canon, credentials, paid operations и real media запрещены исполнителю.
- Исполняемые `task_kind`, `objective` и `task_spec_json` строятся из режима и
  exact-head binding outbox. Старый текст work item служит только источником
  allowlisted focus-метаданных и не может превратить READ_ONLY в REPAIR.
- Отсутствие заранее созданного чата не блокирует роль: каждый `@codex`
  автоматически создаёт отдельную Codex Cloud task/chat.

## Fail-closed

Команда отклоняется при неверном owner/app, PR, head, epoch, role, fingerprint,
scope или schema. ACK имеет тридцатиминутное окно с момента публикации dispatch
PR (миграция 0335). Terminal result имеет
двухчасовое окно после ACK. Без ACK terminal принимается только для всё ещё
`PUBLISHED` dispatch внутри delivery window. В обоих путях обязательны точные
repository, bot/app identity, dispatch id/epoch, role, target PR, fingerprint,
state и (для READ_ONLY/VERIFY) live `target_head_sha`. Истечение окна становится
явной retryable ошибкой, а не ложным успехом; WORLD/Canon и другие semantic
контуры этот транспортный контракт не изменяет.
