# Existing-dispatch transaction rehearsal — 2026-09-24

The current main SQL candidate (strengthened in PR1870) was exercised again on
`misty-poetry-18012774 / br-holy-term-b1k8s9qe / neondb`. Production was not changed.
The original APPLY/ROLLBACK journal already contains two consumed actions; the
new harness does not erase or modify that journal or remove the branch fence.

`python ops/light_dispatch_1867_transaction_rehearsal.py` generates one DO
statement. It copies the current candidate into isolated trial object names and
a separate test event-id namespace, retains its exact branch/owner checks, and
rolls back all DDL, rows, intent and events via an enclosing subtransaction.

Observed with the actual PostgreSQL engine:

- APPLY returns WAITING_EXTERNAL/PUBLISHED, delivery attempts remain5.
- Repeated APPLY rejects with ACTION_ALREADY_USED.
- First synthetic claim succeeds; repeating the same claim returnsfalse.
- ROLLBACK with that intent rejects with RECEIPT_EXISTS; nested transaction
  removes the synthetic intent after rejection.
- Clean ROLLBACK restores every business field. The normal task/work/outbox
  updated_at timestamps differ during the inner test and are deliberately retained.
- Outer rollback restores the exact full snapshot including all timestamps.
- Independent readback: original journal2, trial tableNULL, send intents0,
  original taskFAILED_CLOSED.

Two harness issues were caught before PASS: reuse of old audit event keys
correctly caused AUTOPILOT_EVENT_IDEMPOTENCY_CONFLICT; the initial comparison
forgot task.updated_at. Only the test namespace and timestamp comparison were
corrected. The recovery candidate and its guards were unchanged. All failed
attempts rolled back atomically.

Reproduce only on the named disposable child before its expiry. Never run this
on production. This evidence is not a production-enabled recovery procedure,
actual send, ACK or terminal result.
