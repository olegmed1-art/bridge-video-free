# Isolated Light delivery target, 2026-09-25

Status: DRAFT TARGET ONLY. This file and its draft pull request provide a fresh,
inert GitHub target for an eventual single READ_ONLY delivery observation. They
do not register work, admit a task, lift Light HOLD, authorize publication, or
permit a replay of the completed PR1769 canary.

## Exact boundary

- Use only the draft pull request that introduces this file, at the initial
  reviewed head commit. No force push, edit, merge, or target substitution after
  binding. If the head changes, discard this target and prepare a new one.
- One newly registered task, one dispatch, at most one outbound command and one
  durable send intent. No retry or follow-up after an ambiguous outbound result.
- Requested Codex operation: read this target and report its marker and the
  pinned head SHA. No code change, branch update, merge, deployment, database
  write, student contact, or publication.
- Marker: `LIGHT_READ_ONLY_DELIVERY_CANARY_20260925`.
- Terminal acceptance requires genuine automated ACK and a dispatch-bound
  terminal callback; a manually posted command or synthetic callback is not
  evidence. Check that no second task, outbox, send intent, or command appeared.

## Preflight and stop conditions

Before any task registration or Light admission, independently reconcile the
current main, production Neon default/protected branch, old completed PR1769
task/receipt, empty active queue, current Light process and database login, the
exact draft PR/head and all its comments, and the actual outbound automation's
enabled state and one-send policy. Confirm the current source-side event ledger
and every relevant ingress path since the restore incident #1912. Bind the
unique target and one allowed READ_ONLY command to a separate owner decision.

Keep admission on HOLD if any binding, owner gate, route, queue, source-side
event, send-intent, external sink, ACK or terminal condition is unknown or
contradictory. On any unexpected effect, stop Light in HOLD using the reviewed
emergency path and preserve immutable evidence. No automatic repair or resend.

The separate execution plan must specify how HOLD is opened for exactly this
task and returned to HOLD at the first terminal or timeout. This draft target
does not supply that execution plan or permission.
