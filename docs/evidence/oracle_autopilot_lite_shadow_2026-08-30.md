# Oracle Autopilot Lite shadow — change and verification record

Date: 2026-08-30

Change: `SCHOOL-AUTOPILOT-CONTROLLER-V1 / ORACLE-SHADOW-1.1`

Governance: `ASSURED`

Tracker: #782

Status: `STAGING_LOGIN_PROVISIONED / ORACLE_HOST_UNREACHABLE / NOT ACTIVATED`

Draft PR: #991

## Purpose and scope

Replace the one-hour chat/schedule gap with a persistent, event-driven Oracle
dispatcher while keeping Neon as canonical state. This increment is strictly
shadow-only: no arbitrary shell, model call, GitHub/Drive/media write, canon
change or production route change exists in the executable capability set.

## Implementation

- PostgreSQL schema and bounded RPCs in migration `0300`;
- atomic `SKIP LOCKED` claim, lease heartbeat, fencing epoch and stale recovery;
- `LISTEN/NOTIFY` wake-up with a 30-second recovery poll;
- immediate draining of all READY tasks without a polling delay between tasks;
- immutable task events, external events, evidence and usage records;
- exact event and task idempotency checks;
- external wait/resume/dedupe/expiry;
- pre-call budget reservation and terminal `BUDGET_STOP`;
- least-privilege runtime RPC role with no direct task-table access;
- hardened, `Restart=always`, shadow-only systemd unit;
- staged-by-default installation script.

## Evidence

The final migration was applied in one clean transaction to temporary Neon
branch `br-still-tooth-b1ilkfcj` (`autopilot-shadow-final-20260830`), derived
from the current production parent. No statement was run against the production
branch.

PostgreSQL 18 integration results:

| Check | Result |
|---|---:|
| DONE tasks with retained evidence | 3 |
| OWNER_REQUIRED boundary | 1 |
| BUDGET_STOP before over-cap call | 1 |
| expired external wait → FAILED_CLOSED | 1 |
| satisfied/deduplicated external wait | 1 |
| stale lease fenced and recovered | PASS |
| unfinished step attempts after terminalization | 0 |
| runtime direct SELECT on `autopilot.task` | denied |
| runtime `claim_next_task` RPC | allowed |

Post-review staging preflight:

- the temporary branch was reconciled to repair head `8356725`; the deployed
  `claim_next_task` now excludes exhausted READY rows and
  `ingest_external_event` contains the explicit
  `EXTERNAL_RESUME_BUDGET_EXHAUSTED` terminal path;
- the full SQL state-machine regression passed again under a unique task-key
  namespace inside a savepoint and was rolled back, preserving prior evidence;
- dedicated LOGIN `autopilot_runtime_login` was created only on temporary
  branch `br-still-tooth-b1ilkfcj`, with a four-connection limit and automatic
  expiry at `2026-09-06T15:20:39.243Z`;
- the LOGIN is not superuser, cannot create databases or roles, cannot
  replicate or bypass RLS, has no direct task/event table access, and inherits
  only the bounded runtime RPC capability chain;
- task creation and external-event ingress RPCs remain denied to the Oracle
  execution LOGIN;
- the credential value was not printed, committed, or written to evidence.

Local checks:

- `14 passed` Python contract/model tests;
- Python byte-compilation PASS;
- install-script Bash syntax PASS;
- systemd unit verification PASS (expected missing-path warning before staging);
- JSON parse and `git diff --check` PASS.

GitHub evidence at current PR head `1c4eb4907af7a0f11009d6a323f8d0882e850a69`:

- all eight triggered workflows PASS;
- full Bridge School Database CI PASS on a clean PostgreSQL 18 service;
- Oracle Autopilot Lite shadow CI PASS;
- secret, governance, migration namespace, deployment architecture, META and
  Vercel compatibility gates PASS.

Independent review evidence:

- baseline Vercel Agent review of `7ce601547449cdca2ffa555ea1385e5a2fd9e617`:
  0 suggestions, 7 minutes 2 seconds, USD 1.83;
- focused Vercel Agent review of the same head: 1 actionable logic finding,
  12 minutes 19 seconds, USD 2.13;
- finding: `ingest_external_event` could publish a resumed task as `READY` when
  `attempts = max_attempts`; the next claim incremented beyond the retry budget,
  so a verified external answer could be discarded by the worker contract;
- repair: exhausted resumptions now retain and link the verified event, then
  terminalize explicitly as `EXTERNAL_RESUME_BUDGET_EXHAUSTED`; claims also
  exclude retry-exhausted READY rows;
- regression coverage: PostgreSQL integration proof plus the independent
  bounded state model cover the exact retry-boundary transition;
- repair head `83567256bf91d1b1fd83b4b94c94f9efb2b7dbe1`: all eight
  workflows PASS, including PostgreSQL 18 migration/invariant/idempotence tests;
- exact-head Vercel Agent re-review: 0 suggestions, 3 minutes 15 seconds,
  USD 0.87; the original finding was marked resolved;
- actual Code Reviews cost across the three rows is USD 4.83; the dashboard
  rounds the aggregate display to USD 5.

Assurance:

- I0: implementation self-check PASS;
- I2: independent bounded exhaustive abstract state-model checker PASS;
- I3: external PostgreSQL 18 / Neon state-machine execution PASS;
- independent external Vercel Agent code review: finding repaired and confirmed
  on the exact executable repair head.

## Cost and latency

This increment adds no fixed subscription and performs no model calls. It does
have a possible variable Neon compute cost after activation: a 30-second
recovery query keeps the database compute active. At the current 0.25-CU
minimum and published Scale rate of USD 0.222/CU-hour, the conservative ceiling
for a full 730-hour month is approximately USD 40.52 before subtracting compute
hours already consumed by other school services. This cost mode requires an
explicit activation decision. The temporary Neon branch uses the existing
project and can be removed after the review evidence window.

The code path has no sleep between consecutive READY tasks. Normal wake-up is
event-driven; the target for real Oracle notification-to-claim p95 is at most
five seconds. A lost notification falls back to polling within 30 seconds.
Actual Oracle runtime latency is intentionally not claimed until the service is
staged with its dedicated login.

The temporary branch compute was observed active with a 0.25-CU minimum and
`suspend_timeout_seconds=0`. It must not be mistaken for a free idle staging
state; retaining it while Oracle is unavailable consumes the same variable
compute class discussed above.

## Remaining risk and promotion blocks

- current `main` branch protection is not proven/enforced;
- dedicated LOGIN exists only on the temporary branch and expires automatically;
- Oracle service is not staged or activated: both the protected cloud browser
  and a pinned-fingerprint GitHub runner could not reach the Oracle host;
- real notification/restart latency is not measured;
- incremental always-on Neon compute cost is not approved or measured;
- production canary #881 is not proven;
- Oracle idle-stop guard #627 is not closed.

## Rollback and restoration

Before activation, rollback is deletion of the temporary Neon branch and
removal of the unmerged code branch. After staging, keep
`AUTOPILOT_ACTIVATE=0`; disabling/removing only
`school-autopilot-shadow.service` leaves Assistant Lab, DDS3, BEN and video
services untouched. The temporary LOGIN can be revoked independently and also
expires at the timestamp recorded above. Production database rollback is not
applicable because no production migration has occurred.
