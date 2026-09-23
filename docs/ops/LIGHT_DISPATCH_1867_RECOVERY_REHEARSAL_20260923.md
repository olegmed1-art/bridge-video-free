# Existing-dispatch recovery rehearsal — 2026-09-23

Status: isolated SQL rehearsal PASS; production recovery NOT APPLIED.

## Primary state and test environment

- Task: `f05c605f-f664-4ff7-9927-a039f000a929`, FAILED_CLOSED, attempts 1.
- Dispatch: `322dd440-30b9-49d2-8e1a-f5ecc1d2b99b`, FAILED_CLOSED, attempts 5.
- Existing draft discovery PR: #1867; target PR #1769 remains pinned to
  `2586929313ab40326d64353b513ff86e5ae3350c`.
- The associated work item `714feeda-d4c4-48b1-8d07-26427d343a7b` is BLOCKED
  with `ROLE_DISPATCH_RESPONSE_INVALID`. Restoring only task/outbox would leave
  the protected-send binding unavailable; the work item must also be handled.
- Production parent: `br-wispy-lab-b1rq54of` in project `misty-poetry-18012774`.
- Disposable child: `br-holy-term-b1k8s9qe`, created at 20:08:25 UTC, expires
  2026-09-24 20:08:25 UTC. Compute 0.25 CU, idle suspension 300 seconds.
- Script: `ops/light_dispatch_1867_recovery_rehearsal.sql`, SHA256
  `c6e854e58dc0cd5fe5efaead43d0f5ff2e5f7804f8ee1de9821bcfa7009ee112`.

## Executed checks

| Check | Observed result |
|---|---|
| Exact failed task/outbox/work/step preconditions | PASS |
| Reuse existing PR #1867 with existing publication RPC | PASS |
| Preserve task ID, dispatch ID, target head and attempts=5 | PASS |
| Protected-send binding becomes available for the same assignment | PASS |
| First synthetic send-intent claim | true |
| Same attempt claimed again | false |
| Different attempt claimed for same dispatch | false |
| No synthetic SENT or ACK | PASS |
| Inner transaction rollback restores all four complete row images | PASS |
| Synthetic intent absent after rollback | PASS |
| Drifted attempts=4 | rejected: RECOVERY_REHEARSAL_INPUT_DRIFT |
| Existing send-intent receipt | rejected: RECOVERY_REHEARSAL_RECEIPT_EXISTS |
| Wrong branch identity | rejected: RECOVERY_REHEARSAL_WRONG_BRANCH |

The two negative data cases ran in transactions that rolled back on rejection.
Final child readback: FAILED_CLOSED, attempts=5, publication binding NULL,
send-intent count=0. The script is hard-fenced to this child and rolls back its
own successful experiment. It performs no broker request or GitHub send.

## Remaining production gates

1. Publish and attest the mailbox1703 broker, then pin the deployment URL and
   compatible held worker as specified by the release runbook in PR #1870.
2. Independently re-read PR #1867 author/head/body, target head, service HOLD,
   route, all receipt tables, work/task/outbox/step state and competing work.
3. Implement the administrative production transition with a durable before-image
   archive and recovery event. The rehearsal's temporary snapshots are not a
   production audit ledger. Include guarded rollback that refuses after any
   publication permit, send intent, delivery, ACK or terminal receipt.
4. Use the existing protected-send controller once, then observe the genuine ACK
   and dispatch-bound terminal result. No second broker POST or new discovery PR
   is needed for this already-created dispatch.

This evidence validates the candidate state transition and SQL one-send guard.
It does not prove deployed broker compatibility, production recovery, actual
delivery, ACK or task completion.

## Durable journal candidate — subsequent isolated test

`ops/light_dispatch_1867_journal_candidate.sql` was installed only on the same
disposable child. SHA256:
`f5d922bf0b6fae269ad04bbbe07e74089062f03e3a91304ecb1dfc566426f5df`.
Its installer and callable procedure both refuse any other branch. This is a
reviewable candidate, not a production-enabled recovery route.

The owner-only procedure records complete task/outbox/step/work before and after
images in an append-only journal, plus one task audit event per action. APPLY
adopts the existing PR through the existing publication RPC; ROLLBACK compares
the complete live state with the recorded post-apply state before restoring the
original business fields. Normal `updated_at` changes and the planner's terminal
decision audit are retained. It does not disable triggers or erase history.

Executed database checks:

- APPLY: WAITING_EXTERNAL / PUBLISHED, attempts 5.
- Second APPLY: `RECOVERY_CANDIDATE_ACTION_ALREADY_USED`.
- ROLLBACK with synthetic send intent: `RECOVERY_CANDIDATE_RECEIPT_EXISTS`;
  the negative test transaction rolled back its synthetic intent.
- ROLLBACK after row drift: `RECOVERY_CANDIDATE_ROLLBACK_DRIFT`;
  the negative test transaction rolled back the introduced drift.
- Clean ROLLBACK: FAILED_CLOSED / FAILED_CLOSED, attempts 5.
- Every task/outbox/step/work field except audit timestamp `updated_at` matched
  its original archived value after rollback.
- Journal entries 2, administrative task events 2, send intents 0.
- Total task count stayed 525; no repair or duplicate task was created.
- Journal UPDATE rejected with `AUTOPILOT_APPEND_ONLY`.
- Runtime EXECUTE on recovery procedure: false; runtime journal write: false.

The two actions can be inspected or reproduced on a fresh isolated copy with:

```sql
SELECT autopilot.light_dispatch_1867_recover(
 'APPLY', '{"scope":"ISOLATED_REHEARSAL"}'::jsonb);
SELECT autopilot.light_dispatch_1867_recover(
 'ROLLBACK', '{"scope":"ISOLATED_REHEARSAL"}'::jsonb);
```

The tested child has already consumed both actions; repeating either must fail.
Production enablement still requires a separate reviewed administrative wrapper
that binds fresh broker health, installed held worker, GitHub resource readback
and database route to the operation. Do not remove the child-branch guard merely
to run this candidate in production. The Vercel administrative release blocker
remains unresolved.
