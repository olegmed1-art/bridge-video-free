# School Autopilot Controller v1

Статус: **APPROVED DESIGN / NOT ACTIVATED**  
Версия: **1.0-draft**  
Дата: **2026-08-28**  
Tracker: **#782**  
Governance mode: **ASSURED**  
Владелец цели: директор Школы спортивного бриджа  
Делегированный исполнитель: AI Management System

## 1. Решение

Школа строит долговечный контроллер автономной работы на уже имеющемся стеке:

- **Vercel Pro / Vercel Workflows** — оркестрация, ожидание, повторные попытки и возобновление;
- **Neon** — каноническое состояние задач, событий, шагов, ожиданий, evidence и стоимости;
- **GitHub** — код, PR, CI, workflows и внешнее evidence;
- **Oracle Frankfurt** — DDS3, BEN, media/Python и другие bounded heavy workloads;
- **Google Drive** — источник и архив долговечных материалов;
- **OpenAI Responses API / Agents SDK** — только решения, где недостаточно детерминированного кода.

Vercel Workflow является исполнителем долговечного процесса, но **не каноническим хранилищем состояния школы**. В Neon хранится достаточно состояния, чтобы восстановить ход задачи без памяти чата и без зависимости от конкретной версии workflow SDK.

## 2. Проблема

Обычный чат выполняет только один активный ход. После запуска GitHub, Oracle, Vercel или другой внешней системы чат не может сам проснуться. Директор вынужден вручную отправлять `продолжай`, хотя предметного решения от него не требуется.

Autopilot должен заменить этот цикл:

```text
команда → шаг → внешнее ожидание → результат → следующий шаг → ...
```

и продолжать его до фактического терминального состояния, а не до конца одного ответа ChatGPT.

## 3. Целевой результат

Одна команда создаёт или возобновляет `task_id`. Задача самостоятельно проходит разрешённые шаги и заканчивается только одним из состояний:

- `DONE` — acceptance contract доказан;
- `OWNER_REQUIRED` — требуется ровно одно минимальное действие директора;
- `FAILED_CLOSED` — продолжение небезопасно или evidence недостаточно;
- `BUDGET_STOP` — следующий шаг превысит утверждённый лимит;
- `CANCELLED` — отмена по утверждённой причине.

`WAITING_EXTERNAL` является рабочим состоянием и автоматически возобновляется событием или bounded reconciliation. Оно не является просьбой к директору написать `продолжай`.

## 4. Развёртывание

### 4.1. Отдельный Vercel-проект в существующей Pro-команде

Для Autopilot создаётся отдельный Vercel project, подключённый к тому же GitHub repository, с root directory `autopilot_service/`.

Причины:

- изоляция секретов Autopilot от публичного Bridge School API;
- независимый rollback и deployment history;
- изменения Autopilot не перезапускают основной API без необходимости;
- отдельные spend/observability границы;
- возможность отключить Autopilot, не затрагивая DDS3 и пользовательские endpoints.

Новая подписка не требуется: проект живёт в существующей Vercel Pro team. Deployment запускается только при изменении `autopilot_service/`, общих Autopilot contracts или явно перечисленных зависимостей.

### 4.2. Переносимость

Код зависит от внутреннего интерфейса `DurableOrchestrator`, а Vercel Workflows является первым adapter. Neon state machine, event envelopes и executor contracts не должны содержать Vercel-specific значения, кроме отдельного поля внешнего runtime reference.

## 5. Компоненты

```text
Олег / ChatGPT / GitHub command / School UI
                    |
                    v
        Autopilot API on Vercel
                    |
              create task_id
                    v
       Vercel Durable Workflow
                    |
      +-------------+-------------+
      |             |             |
      v             v             v
    Neon          GitHub        OpenAI
 canonical        CI/PR          reasoning
 state/evidence   evidence       only if needed
      |
      +-------------+-------------+
                    |
                    v
        Oracle / Assistant Lab
      bounded deterministic work
```

### 5.1. Autopilot API

Минимальные endpoints v1:

- `POST /v1/autopilot/tasks` — создать idempotent task;
- `GET /v1/autopilot/tasks/{task_id}` — статус и безопасное резюме;
- `POST /v1/autopilot/tasks/{task_id}/cancel` — bounded cancellation;
- `POST /v1/autopilot/tasks/{task_id}/approval` — решение по точному action fingerprint;
- `POST /v1/autopilot/events/github` — GitHub event ingress;
- `POST /v1/autopilot/events/openai` — OpenAI webhook ingress;
- `POST /v1/autopilot/events/internal` — подписанное внутреннее событие;
- `GET /healthz` — API, DB и workflow compatibility health.

Все task-control endpoints закрыты bearer token или последующей школьной identity-моделью. Входящие webhooks проходят отдельную проверку подписи и не принимают произвольные state transitions.

### 5.2. Neon

Создаётся отдельная схема `autopilot`. Она не заменяет:

- `public.admin_task` — человеческие/операционные задачи школы;
- `assistant_lab.job` — bounded compute jobs;
- `assistant_lab.control_command` — allow-listed Oracle control queue;
- `public.run_checkpoint_event` — технические checkpoints отдельных workloads.

Autopilot связывает эти контуры через immutable references и evidence, но не перегружает их своей orchestration-семантикой.

### 5.3. GitHub

GitHub остаётся:

- источником кода;
- CI и regression boundary;
- механизмом PR;
- хранилищем human-readable evidence;
- аварийным fallback-контуром командных workflows.

Phase 1 использует публичные read-only API и существующие workflow results. Для mutating GitHub API в Phase 2 нужен отдельный GitHub App с минимальными repository permissions. Токен ChatGPT connector не переиспользуется внешним сервисом.

### 5.4. Oracle

Autopilot не получает arbitrary SSH. Выполнение идёт через уже утверждённые каналы:

- `assistant_lab.job`;
- `assistant_lab.control_command`;
- resident workers;
- OCI Instance Agent bounded operations;
- allow-listed local Control API.

### 5.5. OpenAI

Модель не является scheduler, queue, timer или state machine. Она вызывается только при новой неоднозначности, например:

- неизвестный класс ошибки;
- выбор безопасного repair из нескольких вариантов;
- review patch;
- архитектурное или risk-решение.

Ожидание, polling, deduplication, retry, budget enforcement и переходы известных состояний выполняются кодом.

## 6. Каноническая машина состояний

```text
NEW
  -> VALIDATING
  -> READY
  -> RUNNING
       -> WAITING_EXTERNAL
       -> EVALUATING
            -> RUNNING
            -> OWNER_REQUIRED
            -> FAILED_CLOSED
            -> BUDGET_STOP
            -> DONE
       -> CANCELLED
```

### 6.1. Правила переходов

- `NEW → VALIDATING`: только после idempotent task creation.
- `VALIDATING → READY`: schema, policy, capability и budget contract валидны.
- `READY → RUNNING`: workflow получил lease/fencing epoch.
- `RUNNING → WAITING_EXTERNAL`: существует ровно один активный wait contract с provider, correlation ID, deadline и expected event types.
- `WAITING_EXTERNAL → EVALUATING`: подписанное событие принято или reconciliation наблюдает доказанный результат.
- `EVALUATING → RUNNING`: следующий шаг детерминирован и разрешён.
- `EVALUATING → OWNER_REQUIRED`: policy engine доказал owner-only boundary.
- `EVALUATING → FAILED_CLOSED`: capability неизвестна, evidence противоречиво, freshness истекла или безопасного шага нет.
- `EVALUATING → BUDGET_STOP`: зарезервированный следующий расход превышает task/global cap.
- `EVALUATING → DONE`: acceptance contract выполнен и evidence сохранено.

Прямой переход из `WAITING_EXTERNAL` в `DONE` запрещён: результат сначала проходит evaluator/evidence gate.

## 7. Схема данных v1

### 7.1. `autopilot.task`

Текущее материализованное состояние:

- `task_id`;
- `task_key` — уникальный idempotency key внешнего запроса;
- `goal_type`, `goal_version`, `goal_json`;
- `status`;
- `governance_mode`, `risk_class`;
- `current_step_key`;
- `acceptance_contract_json`;
- `allowed_capabilities_json`;
- `workflow_provider`, `workflow_run_ref`, `workflow_version`;
- `lease_owner`, `lease_epoch`, `lease_until`;
- `model_turn_cap`, `input_token_cap`, `output_token_cap`, `cost_cap_usd`;
- `cost_reserved_usd`, `cost_actual_usd`;
- `created_by`, `source`, timestamps;
- `terminal_reason_code` и безопасное резюме.

### 7.2. `autopilot.task_event`

Append-only журнал:

- `task_event_id`;
- `task_id`;
- `sequence_no`;
- `event_type`;
- `state_from`, `state_to`;
- `payload_json` с bounded schema;
- `actor_type`, `actor_ref`;
- `idempotency_key` unique;
- `occurred_at`, `recorded_at`.

Sequence allocation сериализуется advisory lock или эквивалентным RPC, как уже сделано для run checkpoints.

### 7.3. `autopilot.step_attempt`

- `step_attempt_id`, `task_id`, `step_key`, `attempt_no`;
- `executor_type`, `capability_name`;
- `idempotency_key` unique;
- `input_fingerprint`;
- `status`;
- `external_ref`;
- `result_summary_json`, `error_code`;
- timestamps;
- `lease_epoch` для fencing.

Повтор workflow не создаёт второй внешний mutation: перед dispatch выполняется lookup по `idempotency_key` и `input_fingerprint`.

### 7.4. `autopilot.wait_condition`

- `wait_condition_id`, `task_id`, `step_attempt_id`;
- `provider`, `correlation_id`;
- `expected_event_types`;
- `hook_generation` и hash hook token;
- `deadline_at`;
- `status`;
- `last_reconciled_at`;
- `satisfied_by_event_id`;
- timestamps.

### 7.5. `autopilot.external_event`

- `external_event_id`;
- `provider`;
- `provider_event_id` unique;
- `event_type`;
- `correlation_id`;
- `signature_verified`;
- `payload_fingerprint`;
- только безопасный normalized payload;
- `received_at`, `processed_at`.

Raw webhook body хранится только кратковременно для проверки подписи и не становится каноническим task state.

### 7.6. `autopilot.evidence`

- `evidence_id`, `task_id`, `step_attempt_id`;
- `evidence_class`;
- `provider`, `external_ref`;
- `content_sha256` или source fingerprint;
- `metadata_json`;
- `observed_at`, `expires_at`;
- `retained`.

### 7.7. `autopilot.approval`

- `approval_id`, `task_id`;
- `approval_type`;
- `action_fingerprint`;
- точное безопасное описание действия;
- `status` (`REQUESTED`, `APPROVED`, `REJECTED`, `EXPIRED`, `CONSUMED`);
- requester/decider refs;
- timestamps.

Approval нельзя применить после изменения target, parameters, code SHA или evidence set.

### 7.8. `autopilot.usage_ledger`

- `usage_id`, `task_id`, `step_attempt_id`;
- provider/model;
- reserved/actual cost;
- input, cached input и output tokens;
- provider response reference;
- idempotency key;
- recorded_at.

### 7.9. `autopilot.resource_lease`

Долговечная блокировка конфликтующих mutations:

- `resource_key` primary key;
- `task_id`;
- `lease_epoch`;
- `expires_at`;
- `scope_fingerprint`.

Примеры: `github:repo:main`, `oracle:frankfurt:power`, `neon:production:migration`.

## 8. Durable workflow algorithm

Workflow получает только `task_id`; большие payload и история не передаются через Vercel workflow state.

```text
load task
-> acquire/renew task lease
-> validate policy and budget
-> determine next deterministic step
-> reserve idempotency + optional cost
-> execute through approved adapter
-> persist event/evidence
-> if external result required:
     create wait_condition
     suspend on workflow hook
     resume from verified event
-> evaluate result
-> repeat until factual terminal
```

Каждый side effect находится внутри отдельного retryable step, но retry не создаёт новый mutation благодаря idempotency record в Neon.

Exact Python SDK start/resume API и version pin считаются предметом compatibility spike. Ни один unpinned workflow SDK не продвигается в production.

## 9. Event-driven resume и reconciliation

### 9.1. Основной путь

- GitHub webhook / workflow callback;
- OpenAI webhook;
- подписанное событие внутреннего executor;
- Vercel hook resume.

### 9.2. Защита

- проверка provider signature/HMAC;
- timestamp/replay window;
- deduplication по provider delivery/event ID;
- correlation только с существующим active wait;
- normalized allow-list payload;
- hook token является opaque и ограничен одним wait generation;
- accepted event само по себе не авторизует mutation.

### 9.3. Страховочный reconciliation

Небольшой Vercel Cron выполняет детерминированную сверку просроченных `WAITING_EXTERNAL`, например раз в 5 минут:

- проверяет только active waits;
- использует conditional GET/ETag и provider rate limits;
- не вызывает модель;
- возобновляет workflow только при новом доказанном состоянии;
- переводит в `FAILED_CLOSED`, если deadline/freshness contract нарушен.

Таким образом потерянный webhook не оставляет задачу навсегда зависшей.

## 10. Capability и policy boundary

Существующие `CapabilityRegistry` и `AutonomyRouter` являются основой policy adapter. Autopilot не создаёт capability из текста модели.

Для каждого task template фиксируются:

- allowed capabilities;
- разрешённые mutation classes;
- owner-only boundaries;
- required freshness;
- acceptance evidence;
- rollback;
- budget;
- maximum attempts;
- resource leases.

Примеры:

- `github.read` — shadow available;
- `github.write` — Phase 2 после GitHub App;
- `oracle.audit` — read-only;
- `oracle.repair` — только bounded registered operation;
- `oracle.bootstrap`, `account.oauth`, `account.secret.create` — всегда `OWNER_REQUIRED`.

## 11. OpenAI policy и экономия токенов

### 11.1. State capsule

В модель передаётся короткая versioned capsule, а не история чата:

```json
{
  "task_id": "...",
  "goal": "...",
  "current_state": "EVALUATING",
  "last_step": "...",
  "error_code": "...",
  "evidence_refs": ["..."],
  "allowed_actions": ["..."],
  "forbidden_actions": ["..."],
  "budget_remaining_usd": 0.42
}
```

Default maximum capsule size v1: 8 KiB. Большие логи сначала проходят детерминированное извлечение bounded error codes и relevant excerpts.

### 11.2. Model routing

- routine classification/extraction — дешёвая модель;
- обычная диагностика и patch review — стандартная модель;
- P0, security и неоднозначная архитектура — сильная модель;
- известный переход state machine — без модели.

### 11.3. Ограничители

Default shadow task:

- `model_turn_cap = 4`;
- `input_token_cap = 40000`;
- `output_token_cap = 8000`;
- `cost_cap_usd = 0.50`;
- global pilot cap = `$20`;
- no model call after cap reservation failure.

Статические governance/tool instructions располагаются в стабильном prompt prefix для caching. Длинные agent sessions используют compaction, но Neon event/evidence state остаётся первичным источником.

## 12. GitHub write path

Phase 1 не требует GitHub write credential: используется public read-only API и synthetic signed resume.

Phase 2:

1. создать отдельный GitHub App;
2. установить только на `bridge-video-free`;
3. выдать минимальные permissions для contents, pull requests, issues/actions, которые реально нужны template;
4. хранить private key только в Vercel secret store;
5. выпускать короткоживущий installation token;
6. ограничить mutations собственными branches/PR и exact expected head SHA;
7. записывать external refs и action fingerprint.

Fine-grained PAT допускается только как временный pilot fallback и не является целевым production credential.

## 13. Merge boundary

Пока GitHub API сообщает `main.protected=false`:

- Autopilot может читать, создавать branch/PR, обновлять собственный PR и повторять CI;
- Autopilot **не сливает** PR в `main`;
- состояние при готовом PR — `OWNER_REQUIRED` или `WAITING_POLICY`, в зависимости от template;
- production auto-merge feature flag остаётся false.

После ruleset/branch protection отдельный ASSURED gate проверяет:

- обязательные checks;
- expected head SHA;
- absence of unresolved review threads;
- allowed risk class;
- task action fingerprint;
- свежесть evidence;
- rollback path.

## 14. Секреты и principal separation

Целевой набор:

- отдельный Neon principal `autopilot_runtime_principal`;
- доступ только к schema `autopilot` и SECURITY DEFINER RPC;
- отдельный read-only principal для shadow, если практически оправдано;
- `OPENAI_API_KEY` и webhook secret только в Autopilot Vercel project;
- GitHub App secret только после Phase 1;
- ingress/cron secrets отдельны;
- Oracle credentials не передаются в Vercel; используются существующие bounded channels.

Autopilot не расширяет привилегии `bridge_school_app_principal` и `assistant_lab_worker`.

## 15. Первый пилот

### Pilot A — workflow durability, без внешних mutation

1. создать shadow task;
2. workflow записывает `RUNNING`;
3. создаёт `WAITING_EXTERNAL` с opaque token;
4. тестовый signed endpoint отправляет событие дважды;
5. event dedupe принимает один event;
6. workflow возобновляется один раз;
7. evaluator сохраняет evidence и переводит task в `DONE`.

### Pilot B — реальный GitHub read-only wait

1. наблюдать один заранее известный GitHub Actions run публичного repository;
2. workflow засыпает между bounded reconciliations;
3. после terminal conclusion сохраняет run/job refs;
4. не вызывает модель при известном success/failure contract;
5. task доходит до `DONE` или `FAILED_CLOSED` без команды `продолжай`.

### Pilot C — policy boundary

Synthetic task требует `account.secret.create`. Router обязан немедленно дать `OWNER_REQUIRED`, не выполняя обходной путь.

Whole-school Recovery full drill не является первым mutating pilot: сначала доказываются durability, dedupe и policy boundary на shadow/read-only сценариях.

## 16. Tests и независимая проверка

### 16.1. Unit/contract

- все разрешённые/запрещённые state transitions;
- duplicate event;
- duplicate workflow start;
- stale lease/fencing;
- idempotent executor replay;
- approval fingerprint mismatch;
- cap reservation race;
- unknown capability;
- deadline expiry;
- safe normalized webhook payload;
- no raw secret/log persistence.

### 16.2. Integration

- temporary Neon branch;
- Vercel preview/shadow deployment;
- hook suspend/resume;
- forced transient step failure;
- lost webhook + reconciliation recovery;
- workflow redeploy while an old run waits;
- DB reconnect and restart from `task_id`.

### 16.3. Red Team / I2

Перед production promotion:

- property/state-machine checker или независимый formal transition validator;
- отдельный adversarial test pass для replay, concurrency, privilege escalation и budget bypass;
- restore task state from Neon into a fresh workflow instance;
- доказательство, что произвольный текст модели не превращается в capability или shell command.

## 17. Observability

Минимальная панель:

```text
▶ RUNNING
◷ WAITING_EXTERNAL
◆ OWNER_REQUIRED
■ DONE
! FAILED_CLOSED
$ BUDGET_STOP
```

Для каждой задачи:

- age и current step;
- wait provider/deadline;
- retries;
- last evidence;
- workflow run/version;
- token/cost usage;
- owner action, если есть;
- correlation refs GitHub/Oracle/OpenAI.

Alert нужен только при:

- owner required;
- terminal failure;
- stale wait;
- budget stop;
- repeated executor failure;
- invalid signature/replay attack;
- task lease conflict.

## 18. Recovery и rollback

- Canonical task state находится в Neon и входит в независимый DB backup.
- Human-readable architecture/contracts находятся в GitHub.
- Workflow payload содержит только task ID и минимальные runtime refs.
- При Vercel outage новый workflow может быть запущен по незавершённому task ID.
- GitHub command workflows остаются fallback до отдельного observation window.
- Autopilot project можно отключить без остановки Bridge School API, DDS3 или Oracle workers.
- Phase 1 rollback: удалить shadow deployment/temporary branch; production schema не меняется.
- Production migration применяется только после временной Neon branch, SQL tests и explicit migration gate.

## 19. Этапы продвижения

### Phase 0 — DESIGN

- issue #782;
- этот документ;
- machine-readable project state;
- portfolio entry;
- migration namespace reservation;
- runtime unchanged.

### Phase 1 — SHADOW

- отдельный Vercel project;
- Python Workflows compatibility spike;
- temporary Neon schema;
- Pilot A/B/C;
- no production mutation;
- cost target near zero.

### Phase 2 — BOUNDED WRITE

- GitHub App;
- own branch/PR/CI repair;
- no main merge;
- first real technical task template.

### Phase 3 — CONTROLLED PRODUCTION

- protected main;
- gated merge for low-risk templates;
- one reliability canary;
- observation window and rollback test.

### Phase 4 — SCALE

- recovery, video, research and audit templates;
- 10/30/100-user load tests;
- service SLO and cost review;
- optional Workspace Agent bridge.

## 20. Promotion gates

Phase 1 begins only when:

- design and namespace pass CI;
- Python Workflow SDK start/suspend/resume is proven in preview;
- dedicated secret/principal plan exists;
- temporary Neon migration is reversible;
- no new fixed spend is created.

Phase 2 begins only when:

- all shadow acceptance tests pass;
- event dedupe and replay protection are observed;
- state recovery from Neon is proven;
- Red Team reaches I2;
- director performs only unavoidable account-secret setup.

Phase 3 begins only when:

- main branch protection is proven;
- required checks are stable;
- action fingerprint/approval tests pass;
- rollback and budget stop are observed;
- no task can silently modify bridge canon.

## 21. Success metrics

- director continuation commands per technical task decrease by at least 80%;
- external wait resumes without director action in at least 95% of eligible waits;
- duplicate side effects: zero;
- unknown/unapproved capability executions: zero;
- owner escalations contain one concrete minimal action;
- model-free transitions exceed 80% of total transitions;
- median state capsule remains under 8 KiB;
- pilot stays within the approved cost cap;
- every `DONE` task has retained acceptance evidence;
- task state is reconstructable without chat history.

## 22. Kill / pause criteria

Pause promotion if:

- director workload increases rather than decreases;
- duplicate or untraceable mutation occurs;
- state cannot be recovered independently from chat/Vercel runtime;
- webhook replay or privilege boundary fails;
- model cost cannot be bounded before call;
- the controller needs arbitrary shell to progress;
- deployment coupling harms the existing Bridge School API;
- evidence shows Vercel Workflows is not mature enough for required durability.

In that case the fallback is the existing GitHub command workflows plus Neon state, while Inngest is reassessed as the next adapter rather than rewriting executor contracts.
