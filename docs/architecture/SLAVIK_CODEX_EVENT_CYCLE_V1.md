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
   публикует один owner-authenticated `@codex` comment в **target PR**.
4. GitHub создаёт отдельную изолированную Codex Cloud task/chat. Реальная
   доставка фиксируется только после `eyes` reaction от закреплённого Codex bot
   (`id=199175422`).
5. GitHub Actions повторно проверяет открытый target PR, base `main` и exact
   head, после чего узкий `SECURITY DEFINER` RPC переводит outbox в `SENT`.
6. Codex завершает ответ блоком `AUTOPILOT_CODEX_RESULT_V1`. Второй callback
   принимает только comment закреплённого GitHub App (`id=1144995`), повторно
   сверяет live head и требует сохранённый ACK.
7. Neon атомарно сохраняет terminal receipt/evidence, закрывает task/work item
   и посылает `NOTIFY` для следующей независимой задачи.

## Инварианты

- `PUBLISHED` означает только существование dispatch PR, не доставку.
- Один `dispatch_id` получает не более одного ACK и одного terminal receipt;
  повтор точного payload идемпотентен, конфликтующий повтор отклоняется.
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
scope или schema. ACK имеет десятиминутное окно. Terminal result имеет
двухчасовое окно, принимается только после ACK и только при совпадении live head
с заявленным `target_head_sha`. Истечение окна становится явной retryable
ошибкой, а не ложным успехом.
