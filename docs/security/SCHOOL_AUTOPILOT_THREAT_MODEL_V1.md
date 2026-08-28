# School Autopilot Controller v1 — threat model

**Статус:** DESIGN / NOT ACTIVATED  
**Дата:** 2026-08-28  
**Связанный дизайн:** `docs/architecture/SCHOOL_AUTOPILOT_CONTROLLER_V1.md`

## 1. Защищаемые активы

1. Канонические знания и методика Школы.
2. Production state в Neon.
3. Исходные видео, документы и результаты в Google Drive.
4. GitHub repository, workflows, branch history и evidence.
5. Oracle compute host, DDS3/BEN/video runtime и host-bound secrets.
6. Vercel production API и будущий Autopilot control plane.
7. OpenAI API credentials, prompts, model traces и cost budget.
8. Approval владельца и его точный scope.
9. Append-only task/event/evidence history.
10. Финансовые лимиты и уведомления.

## 2. Доверительные границы

```text
User / ChatGPT
      |
      | authenticated task request
      v
Vercel Autopilot API
      |
      | least-privilege DSN
      v
Neon autopilot schema

Vercel Autopilot <-> GitHub App / Actions
GitHub Actions -> Oracle allow-listed operations
GitHub / Oracle / OpenAI -> signed callbacks -> Vercel
Drive bytes -> Oracle / durable storage, never through Vercel control state
```

Каждая стрелка является отдельной trust boundary и требует authentication, authorization, idempotency и bounded payload.

## 3. Основные противники и сбои

- внешний неавторизованный отправитель webhook;
- повторная доставка или replay действительного event;
- скомпрометированный provider token;
- prompt injection из issue, PR, log, Drive document или model output;
- ошибочное решение модели;
- ошибка кода state machine;
- race между двумя workers;
- partial failure после внешнего действия и до записи receipt;
- stale approval для изменившегося действия;
- чрезмерные retries и cost runaway;
- изменение workflow/branch после approval;
- утечка secret в log, state capsule или evidence;
- потеря callback;
- Vercel redeploy во время ожидания;
- Neon outage;
- GitHub/Oracle/Drive/OpenAI outage;
- вредоносный или ошибочный arbitrary shell/SQL/URL;
- автоматическое изменение предметного канона;
- supply-chain изменение action/package/model;
- ошибочная multi-tenant изоляция при будущем масштабировании.

## 4. Threats и обязательные controls

### T01 — forged callback

**Риск:** злоумышленник переводит задачу в следующий state или инициирует write.

**Controls:**

- provider-native signature verification;
- exact expected provider/repository/project;
- timestamp freshness;
- nonce/event id uniqueness;
- correlation id и task wait record;
- reject unknown event types;
- payload size limit;
- fail closed before state transition.

### T02 — replay / duplicate delivery

**Риск:** повторный external effect, PR, dispatch, restore или оплата.

**Controls:**

- unique `(source, source_event_id)`;
- per-effect idempotency key;
- outbox + receipt;
- optimistic state version;
- read-before-write для API без idempotency;
- duplicate event recorded as rejected evidence.

### T03 — worker race

**Риск:** два workers одновременно выполняют один step.

**Controls:**

- short lease with owner and expiry;
- transactional claim;
- compare-and-swap `state_version`;
- heartbeat только для bounded active step;
- stale lease recovery test;
- external effect idempotency independent of lease.

### T04 — partial failure after effect

**Риск:** внешнее действие выполнено, но controller считает его невыполненным.

**Controls:**

- outbox created before send;
- stable idempotency key;
- provider receipt stored separately;
- reconciliation reads actual provider state;
- no blind retry of destructive effect;
- ambiguous outcome -> `FAILED_CLOSED` or `OWNER_REQUIRED`.

### T05 — prompt injection from external content

**Риск:** issue/log/document убеждает model расширить permissions или нарушить governance.

**Controls:**

- external text always classified as untrusted data;
- tool allow-list defined in code, not prompt;
- model cannot create new tool/task kind;
- structured state capsule separates instructions from evidence;
- no secrets in model context;
- bounded excerpts rather than full raw logs/documents;
- risk classifier and deterministic policy after model output;
- canon and owner-only rules override model recommendation.

### T06 — unsafe model output

**Риск:** модель предлагает destructive, costly or canon-changing action.

**Controls:**

- strict JSON schema;
- deterministic policy engine validates action;
- risk class cannot be lowered by model;
- unknown action rejected;
- critical operation requires exact approval scope digest;
- model output is recommendation until executor policy accepts it;
- max model turns and budget.

### T07 — approval confusion / stale approval

**Риск:** прежнее approval применяется к изменённому commit, diff или command.

**Controls:**

- approval binds to SHA-256 `scope_digest`;
- includes repository, head SHA, operation, parameters and risk class;
- TTL;
- one-time or explicitly reusable flag;
- changed head SHA invalidates approval;
- approval history append-only.

### T08 — secret exposure

**Риск:** credential попадает в GitHub issue, Vercel log, Neon state или OpenAI trace.

**Controls:**

- separate Vercel project secrets;
- minimum credentials;
- no OCI private key in Vercel v1;
- root/host-bound Oracle secrets remain unchanged;
- redaction before persistence/model;
- secret-pattern gate in CI;
- hashed resume tokens;
- no raw dumps or video bytes in controller state;
- rotation runbook.

### T09 — arbitrary execution

**Риск:** controller превращается в remote shell/SQL runner.

**Controls:**

- registered task kinds only;
- typed executors only;
- no arbitrary command string;
- no arbitrary SQL from task payload or model;
- no arbitrary URL fetch;
- Oracle only via existing allow-listed GitHub workflows in v1;
- task kind changes require code review.

### T10 — canon boundary violation

**Риск:** технический агент изменяет систему торговли, методику или учебный канон.

**Controls:**

- explicit canon boundary tests;
- path/content classification;
- any semantic canon change -> `OWNER_REQUIRED`;
- model confidence does not bypass owner;
- technical state cannot write canonical subject tables;
- dedicated least-privilege principal.

### T11 — cost runaway

**Риск:** infinite retries/model loops/heavy compute.

**Controls:**

- per-task and monthly budget;
- max external attempts;
- max model turns;
- deadline;
- model router chooses minimal sufficient model;
- deterministic paths have zero model calls;
- 50/75/90/100% alerts;
- `BUDGET_STOP` terminal state;
- provider hard spend limits.

### T12 — callback loss / provider outage

**Риск:** задача остаётся ждать навсегда.

**Controls:**

- deadline and reconciliation schedule;
- provider status read on bounded cadence only after missed callback;
- exponential backoff;
- no tight polling;
- external correlation id;
- `WAITING_EXTERNAL` observability;
- owner notification only after controlled threshold.

### T13 — deployment/redeploy interruption

**Риск:** Vercel deploy destroys in-memory progress.

**Controls:**

- no canonical state in process memory;
- durable workflow checkpoints;
- Neon state and append-only events;
- redeploy-during-wait acceptance;
- versioned workflow/task contract;
- migration compatibility gate.

### T14 — supply-chain compromise

**Риск:** unpinned action/package/model behavior changes.

**Controls:**

- pin GitHub Actions by immutable SHA;
- lock dependencies;
- verify checksums where practical;
- minimal dependency set;
- model names controlled by environment policy and observed deployment version;
- dependency update PRs with CI;
- no runtime package install from untrusted task input.

### T15 — repository takeover / unsafe merge

**Риск:** Autopilot merges malicious or unexpected code.

**Controls:**

- no auto-merge in initial phases;
- branch protection/ruleset is a hard gate;
- required checks;
- expected head SHA;
- only own PR;
- bounded paths and risk LOW;
- merge permission separated from write permission;
- observation window and kill switch.

### T16 — future tenant data leak

**Риск:** user A sees task/evidence of user B.

**Controls before multi-tenant activation:**

- tenant id on every record;
- row-level security or isolated principals;
- authorization tests;
- no user-supplied evidence URI without ownership validation;
- separate budgets/quotas;
- multi-tenant mode disabled in v1.

## 5. Data classification

| Class | Examples | Persistence | Model access |
|---|---|---|---|
| PUBLIC | public docs, public PR metadata | allowed | allowed |
| INTERNAL | task state, non-sensitive error codes, hashes | Neon/GitHub evidence | bounded |
| SENSITIVE | student/admin metadata, private Drive ids, internal URLs | approved encrypted stores | minimized/redacted |
| SECRET | API keys, DSNs, OAuth tokens, private keys | provider secret stores only | forbidden |
| LARGE_BINARY | video/audio/database dump | Drive/OCI only | references/excerpts only |
| CANON_REVIEW_REQUIRED | semantic bidding/methodology changes | canonical process only | recommendation only; owner required |

Machine-readable policy: `ops/autopilot/data-classification.yml`.

## 6. Residual risks accepted for pilot

1. Provider webhook delivery can be delayed; reconciliation remains required.
2. Vercel Workflows is a platform dependency; task/event state in Neon permits migration.
3. GitHub remains a central execution/evidence dependency.
4. Shadow recommendation quality is not yet proven.
5. No automatic production merge is allowed, so some final actions remain manual until branch protection evidence exists.

## 7. Explicitly unaccepted risks

- autonomous canon change;
- arbitrary shell or SQL;
- unbounded model loop;
- destructive action with ambiguous receipt;
- secret exposure to model;
- auto-merge without protected `main`;
- large raw data routed through Vercel;
- direct production OCI mutation from Vercel in v1;
- task marked PROVEN by inference rather than observed acceptance.

## 8. Security acceptance

Before any bounded production write:

1. forged signature tests;
2. replay and duplicate event tests;
3. stale approval test;
4. changed head SHA invalidates approval;
5. two-worker race test;
6. partial-effect reconciliation test;
7. prompt-injection corpus test;
8. secret scanning of logs/state/model traces;
9. budget-stop test;
10. kill-switch test;
11. unknown event/task kind fail-closed;
12. canon boundary regression;
13. branch protection evidence;
14. dependency and action pinning gate.

## 9. Incident response

```text
kill switch
 -> stop new claims
 -> preserve state/evidence
 -> revoke affected credential
 -> identify exact external effects
 -> reconcile actual provider state
 -> restore from known-good code/state
 -> regression test
 -> update threat model and automated protection
```

Significant incident must follow the technical governance incident rule and must not be closed solely on a documentation update.
