# Provider timeout recovery must consume the audit retry budget

## Confirmed defect

At 2026-09-22 04:31 UTC, #1059/#1599/#1683 were paused with provider deadline
codes and already retained one progress receipt for their current heads.
Migration 0368 fenced CI/invalid-response audit retries but omitted the three
provider failure codes from its legacy-RPC guard and bounded candidate list.
The legacy 0353 path accepts a newer global CODEX success timestamp and sets
work READY without consuming a per-head progress receipt. Repeated provider
successes change the evidence token, allowing repeated same-head admissions.

Independent I2 diagnosis confirmed this path. A new successful transport canary
could therefore rearm all three old audits. #857 is separately protected by its
REPAIR/VERIFY lineage, and #1064 remains WAITING_DEPENDENCY.

## Containment and correction

The GitHub workflow `Autopilot paused evidence reconciliation` was disabled
before registering a canary. The resident worker, callback receiver, and protected
bridge were left active. No old work status, retry receipt or dispatch was reset.

Migration 0371 adds provider deadline/generic-failure audit outcomes to the
existing bounded progress controller and removes global-health-only legacy rearm
for every task kind, including unsupported non-audit work. Verified target
disposition closure retains its existing gates. It
requires a newer accepted non-provider-failure result on the exact same transport
route. A global circuit timestamp or green CI is insufficient. The existing
atomic work/head receipt and lifetime cap remain authoritative. Existing same-head
receipts produce RETRY_BUDGET_EXHAUSTED. No task, work or dispatch data is rewritten
by the migration, and no new execution grants are added.

Regression tests run on disposable PostgreSQL 18 with real task triggers. They
exercise all three failure codes, global-health/CI false evidence, legacy bypass,
same-head receipt retention, provider-failure callback rejection, wrong-route
rejection, owner holds, unsupported task kinds, retained disposition closure,
one positive admission and stale-snapshot replay. The 0353 regression now expects
global provider health alone to preserve PAUSED rather than grant a retry.

Rollback restores the saved function definitions but retains all progress and
send-intent receipts. It is unsafe to resume the old reconciler after rollback;
pause/quiesce it before rollback and keep it disabled until a corrected gate is
present. Restoring the old code is not evidence of safe recovery.

## Acceptance still required

After exact-head CI and independent review, apply the verified migration, check
the deployed definitions and existing holds read-only, then resume reconciliation.
The separate draft PR #1769 is the bounded new canary target. Its actual delivery
and authenticated terminal acceptance must be observed; this patch alone is not
a successful end-to-end result or permission to replay an expired dispatch.
