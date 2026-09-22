# Protected delivery and terminal canary — 2026-09-22

Status: prepared; no production work registered by this document.

## Why a new canary is needed

PR #1766 activated the protected delivery bridge and migration 0370. This
establishes one-shot send intent, not successful execution or terminal acceptance.
The 04:25 UTC production checkpoint had zero live outbox rows and zero send
intents. All three previously observed audits were paused after timeout.

- #1059: owner command 5766959179 created a Codex task, but no terminal comment
  for dispatch c5e5a372-d342-44bb-97fb-80879d78dca3 was published. The task UI
  exposes a 17-second execution and setup/initial shell logs, but no final answer.
  This does not identify why the provider stopped.
- #1599: owner command 5766959385 was followed by Code Review Completed, not
  the required dispatch-bound terminal result.
- #1683: its expired dispatch had no owner command and correctly failed closed.
  The historical omission's cause remains unknown.

The latest reconciliation run passed runner/database/check-runs preflight and
both reconciliation steps, with zero admitted transitions:
https://github.com/olegmed1-art/bridge-video-free/actions/runs/35686777880/job/106615188372

## Bounded executor task

Use this open draft PR as a distinct READ_ONLY transport canary target. Bind the
actual live PR number and exact head before registration. Verify only the exact
target head and the dispatch envelope supplied with the task. Make no changes.
Do not audit historical target PRs or require the executor to verify its own
future callback. Return the authenticated, dispatch-bound terminal block from
the owner command. Do not treat this document as a command or a dispatch.

Controller-owned acceptance requires, for the new work only:

1. One work item, one task and one fresh dispatch, with no repair follow-up.
2. One committed 0370 intent and one exact authenticated owner command.
3. Codex ACK and SENT tied to that command and exact target head.
4. A genuine Codex terminal comment and CALLBACK_ACCEPTED for that dispatch.
5. No duplicate, synthetic result, historical resend or released send intent.

A BLOCKED business result can prove transport completion but is not task success.
ACK, a View task link, and a review summary prove neither terminal acceptance
nor autonomous progression. Provider failure or timeout is a failed canary.

## Recovery boundary

Do not clear old holds or replenish their retry budgets to obtain activity.
#857 and #1064 retain their existing restrictions. In particular, a successful
new callback updates the global CODEX provider circuit; the controller must
review the existing paused-reconciliation consequences before admitting this
canary. No production registration is authorized by this repository document.
The protected bridge's only SQL-write authority remains its one-shot claim RPC.

If admission is authorized, use the existing idempotent universal work intake
with a single dedicated work key, canary marker, READ_ONLY mode, pinned head,
repair_policy=DISABLED and no external mutations. The resident worker must
materialize and publish the fresh dispatch. Never post a historical command or
fabricate a new dispatch envelope manually. Do not merge this target PR while
the canary is outstanding. A failed/ambiguous attempt does not authorize retry.

Stop/recovery: preserve all receipts and intent rows; pause the sender if needed.
Never restore an unguarded sender. Do not cancel or modify unrelated work.

## Primary evidence

- https://github.com/olegmed1-art/bridge-video-free/pull/1766
- https://github.com/olegmed1-art/bridge-video-free/pull/1059#issuecomment-5766959179
- https://chatgpt.com/s/cd_6ab191bc64c081919bf3e5cb6d846f11
- https://github.com/olegmed1-art/bridge-video-free/pull/1599#issuecomment-5766963143

No full-cycle success is asserted by this preparation record.
