# Autopilot online observer live pilot — 2026-09-01

## Outcome

The Oracle-resident online observer is installed and running as a separate
supervised `SHADOW_ONLY` service. It continuously creates only zero-cost
`AUTOPILOT_SMOKE_V1` tasks through the guarded Neon RPC. The existing
`school-autopilot-shadow.service` consumer was not stopped or restarted, and
the Oracle instance lifecycle was not changed.

## Exact installation evidence

- Observer source revision: `92dfbb34b61ed32d045b90f7310665449ea55307`.
- Existing consumer revision: `d908565a5de4481f0bf9a1d2a489f04f8893af65`.
- Exact source pull-request CI: 14/14 PASS, including PostgreSQL 18.
- Fresh read-only consumer diagnostic: workflow run
  [33480006247](https://github.com/olegmed1-art/bridge-video-free/actions/runs/33480006247) — PASS.
- Observer installation and live heartbeat: workflow run
  [33480237060](https://github.com/olegmed1-art/bridge-video-free/actions/runs/33480237060) — PASS.
- Observer service: active, enabled, `NRestarts=0` after the activation window.
- Observer journal: exactly one startup marker and zero traceback, runtime,
  or circuit-open markers during activation.
- Existing consumer: active and enabled; `MainPID` and `NRestarts` matched
  their pre-install values after observer activation.
- DDS3 readiness non-regression: PASS.
- Oracle instance stop requested: NO.

## Temporary Neon boundary

Only project `misty-poetry-18012774`, temporary branch
`br-still-tooth-b1ilkfcj`, database `neondb` was targeted.

- Migration 0304, guarded online state/RPC/circuit: present.
- Migration 0305, exact `last_task_id` result identity: present.
- Runtime table access: denied.
- Runtime guarded tick/status RPC access: granted.
- Default or production branch targeted: NO.

## Three-minute live checkpoint

Observed interval: `2026-09-01T07:02:57.078Z` through
`2026-09-01T07:06:17.960Z`.

- Created: 41.
- `DONE / ACCEPTANCE_EVIDENCE_RETAINED`: 41/41.
- Active at checkpoint: 0.
- Maximum attempts used: 1.
- Average task cycle: 92.6 ms.
- Maximum task cycle: 114.9 ms.
- Maximum creation gap: 5,033.2 ms.
- Retained-evidence violations: 0.
- Terminal-reason violations: 0.
- Model-call violations: 0.
- Production-mutation violations: 0.
- Actual cost: 0 micro-USD.
- Durable findings: 0.
- Circuit open: NO.

The service remains supervised and continues the live pilot after this
checkpoint. It permits at most one active task and opens the durable circuit
on the first invalid terminal result, stale lease, unsafe wait, or evidence
violation.

## Required-fix ledger

### F-001 — chat process was not a durable online controller

- Status: RESOLVED.
- Evidence: the earlier interactive loop had a 98.548-second maximum creation
  gap because its lifecycle depended on the chat runtime.
- Fix: move generation and observation to the separate Oracle `systemd`
  observer with one-task, rate, circuit, cost, model-call, and production
  mutation guards.

### F-002 — first observer bundle omitted a runtime policy dependency

- Status: RESOLVED.
- Safe failure: workflow run
  [33479015463](https://github.com/olegmed1-art/bridge-video-free/actions/runs/33479015463)
  stopped during preflight before installing the unit.
- Fix revision: `124638ab3c6941ca48e77174562ca4d39ac09894` includes
  `autopilot_phase3b` in the pinned bundle and immutable release.

### F-003 — same-transaction result lookup was nondeterministic

- Status: RESOLVED.
- Evidence: PostgreSQL CI exposed that two tasks created in one transaction
  share transaction time, so ordering by `created_at, task_id` could select a
  random UUID.
- Fix revision: `8bef5121d9050f5e3dd9b62f7d840dc09fed7882` and
  migration 0305 resolve the task through the exact `last_task_id` retained in
  pilot state.
- Validation: transactional temporary-branch invariant PASS with forced
  rollback, followed by 14/14 exact-head CI PASS.

## Preserved boundaries

- Runtime mode: `SHADOW_ONLY`.
- Production Neon mutation: NO.
- Production routing change: NO.
- Merge or force-push: NO.
- Main branch mutation: NO.
- Video, ASR, OCR, DDS, BEN, TRAIN, or model workload: NO.
- Secret value read, logged, or committed: NO.
