# Global School Observer: current evidence and notification preview

Date: 2026-10-19. Scope: existing read-only Observer (#1157), ASSURED.

## Problem and retained evidence

[Shadow run 39241231676, number 197](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39241231676)
reported zero findings at `2026-10-19T10:58:17.207521+00:00`. Its snapshot
contained only the old #881 installation receipt, merged PR #1158, and an OPEN
#1157 task. The first two were unconditionally SUCCESS; freshness rules only
examined active tasks. Thus successful execution and zero findings did not
establish current school health.

[The prior quality review](https://github.com/olegmed1-art/bridge-video-free/issues/1157#issuecomment-5640371238)
explicitly left notification quality unaccepted. That is historical context,
not a runtime authorization or proof that current services are installed.

## Minimal correction

- Keep the existing evaluator, GitHub GET transport, schedules and read-only permissions.
- Resolve current main once; read the three existing governance/index JSON files at that SHA.
  Use embedded verification dates for operational indexes (24-hour window, matching
  the daily cross-system reconciliation baseline), not HTTP retrieval time.
- Observe #620 and #1157 as current tracker state. An open overdue audit and an open
  acceptance tracker are BLOCKED. A tracker closed as completed is COMPLETED only
  in that scope; cancellation is not completion. Missing or invalid evidence is UNKNOWN.
- Emit CONFIRMED / STALE / BLOCKED / COMPLETED / UNKNOWN source assessments.
  A green job means the observation receipt is valid; findings separately determine
  `ATTENTION_REQUIRED` or `NO_FINDINGS_IN_OBSERVED_SCOPE`.
- Form notification text in memory with evidence, observation time, owner and next action.
  Keep `notifications_enabled=false`, `delivery_status=NOT_ATTEMPTED` and explicit
  notification-quality non-acceptance. This change has no delivery adapter.
- Preserve stable dedupe keys across observation times and unrelated main commits
  by using file blob identities. Preview suppression covers the supplied input and
  explicitly supplied prior keys; no persistent cross-run suppression is claimed.

## Verification

Command: `PYTHONPATH=autopilot_service python -m unittest discover -s autopilot_service/tests -p 'test_observer*.py' -v`.
27 observer tests pass locally. The tests cover retained audit facts, confirmed/stale/blocked/completed/unknown
states, missing/future/invalid timestamps, unavailable APIs, malformed indexes,
cancelled versus completed trackers, stable dedupe, actual notification text,
GET-only transport and local receipt output. ERROR and LOOP_DUPLICATION formation
are also exercised. Network connections are forbidden during preview tests.

Independent Red Team review (separate model, I2 under AGENTS.md) identified a
truncated-HTTP-response gap: `IncompleteRead` was not caught as a source failure.
The collector now catches `HTTPException`, preserves a sanitized UNKNOWN result,
continues independent reads and forms the receipt. A regression reproduces the
truncated-response case and asserts continued observation and preview formation.
The independent re-review passed all 27 tests and separately reproduced both
`IncompleteRead` and `BadStatusLine`: all six reads continued and previews were
formed with UNKNOWN source evidence. Final verdict: no remaining actionable
findings in the reviewed scope; runtime health and external delivery are unproven.

A bounded live GET-only pre-merge observation at `2026-10-19T17:30:30.414026+00:00`
read main `45a129f13f54923bbdd418e721731326675279d6` and produced four findings:

| Source | Observed assessment | Evidence |
| --- | --- | --- |
| Portfolio | STALE; verified 2026-08-30 | blob `dde06f165214b2ed3379affaf23035720556da73` |
| Autopilot project-state | STALE; verified 2026-09-01 | blob `4d308481e784735fe4d68183f0f5dc92da56a4d1` |
| Governance audit #620 | BLOCKED; open, due 2026-09-25 | issue #620 and governance blob `97d41e1578a67252ada2f5a973534b1bdfddf8c5` |
| Observer acceptance #1157 | BLOCKED; open | issue #1157 |

Four notification previews were formed, zero delivered. Full application contract
tests require the existing compatibility CI dependencies; the local environment
does not have FastAPI. See the associated PR checks for exact-revision CI and review.

## Boundaries, remaining work and rollback

Runtime services, Neon queues and external notification delivery are explicitly
unobserved. No Legacy, Native or Diana20 services, credentials, permissions,
network policy, Canon, spending, or historical dispatch PRs are changed.
The old operational index dates are deliberately not refreshed without actual
reconciliation by their current owners. The overdue governance audit remains open.

Positive dry-run evidence proves formation, not delivery quality or authorization.
Notification activation still requires the #1157 acceptance decision and applicable
separate safety confirmation. Do not infer readiness from a merged PR or green job.

Rollback: revert this PR's Observer code, tests and receipt schema changes together;
no data migration or service operation is needed. The prior observer would again
have limited historical coverage, so retain this limitation in any rollback report.
