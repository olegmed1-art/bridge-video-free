# Codex bridge delivery hardening — 2026-09-21

Base: `c9fa6a61a236c06fab8036204a64bacbcaa02404`.
Scope: dormant owner-command delivery fix. No production SQL writes, automation
cutover, grants, server changes, replays, or deadline extensions performed.
ASSURED / I2. Production migration and the bridge's new single-claim SQL
exception require separate explicit owner approval; the previous prohibition
on production SQL writes remains effective.

## Primary evidence and limits

- Dispatch [PR #1762](https://github.com/olegmed1-art/bridge-video-free/pull/1762)
  was created at 20:15:40 UTC. Production outbox recorded PUBLISHED at
  20:15:41.950 UTC. Its command/ACK remained absent; the worker closed it at
  20:45:53.674 UTC with CODEX_ACK_DEADLINE_EXCEEDED. At 21:01:02.558 UTC the
  work item became PAUSED / PROVIDER_HOLD. One task/dispatch/progress receipt.
- The bridge's visible last execution reports only #1059 and #1599 delivered.
  This does not prove whether #1762 was omitted by event delivery, batching,
  preflight, or execution. The precise historical root cause remains UNKNOWN.
- `process_role_dispatch_outbox` publishes the GitHub resource before calling
  `mark_role_dispatch_published`. The existing bridge prompt required PUBLISHED
  immediately, without a bounded re-read. This is a reproducible ordering gap,
  not proof it caused this particular incident.
- #1599 has ordinary Codex review, not AUTOPILOT_CODEX_RESULT_V1. No replacement
  terminal is synthesized; #1059/#1599 deadlines and existing commands remain.

## Change

The initial prompt-only v2 was rejected by independent I2 review: concurrent
invocations could both read no comment and both POST. Its model tests did not
prove cross-run exclusion. V2 is superseded, not activated.

Migration 0370 adds an owner-only SECURITY INVOKER binding/claim API and a
retained one-shot ledger. It does not grant any new principal access, change
task/outbox state, create an ACK, unlock work, or change retry budgets. A unique
dispatch_id allows one fresh authorization response only; even an identical
attempt cannot receive a second true. The actual canonical assignment is
obtained through get_dispatch_assignment, including mode-specific restrictions.

The staged v3 prompt consumes only supplied events, checks ready candidates
before waiting, uses at most 77 seconds of readiness waiting for the whole
batch, and isolates candidate failures. Immediately before claiming it checks
all comment pages, exact heads, live authority and a 180-second margin.
Only a fresh committed true, followed by exact ledger readback in the same
uninterrupted invocation, allows one POST attempt. Unknown claim/commit/POST
outcome permits readback only, never retry or takeover.

`github_codex_command.py` provides deterministic rendering and exact full-actor
readback, reusing the existing callback parser. Execution policy is pinned from
[owner command 5766959179](https://github.com/olegmed1-art/bridge-video-free/pull/1059#issuecomment-5766959179),
with only the five dispatch-specific terminal fields converted to placeholders.
Template SHA256: `123c9a6f82b69bfe3c46bacf65152c94d77b31b72e00a6b5ffa66ac69e80c902`.

Guarantee: at-most-one authorized application send attempt among claim-aware
senders after all legacy senders have been quiesced. NOT exactly-once delivery.
A lost successful claim reply can intentionally cause a missed delivery rather
than a duplicate. A durable claim is not evidence that any HTTP POST occurred.
The hosted-agent execution of the protocol still requires a future natural
event canary. No historical root cause or successful repair is inferred from
the presence of this code.

## Rollback

Pause/quiesce the bridge. Never restore the unguarded POST path. The 0370
rollback fences claim transactions and drops the functions while retaining
every ledger row; reapply must not reopen consumed authority. No ledger deletion,
claim release, expired dispatch replay or server rollback is authorized.

## Verification / activation

Status: DRAFT / NOT APPROVED FOR ACTIVATION. The live bridge prompt is unchanged.

The targeted local regression run passed 85 tests:
`python -m pytest -q tests/test_oracle_autopilot_bridge_prompt.py tests/test_oracle_autopilot_github_codex_callback.py tests/test_oracle_autopilot_github_codex_callback_workflow.py`.
The database CI adds real PostgreSQL invariant/concurrency tests: eight callers,
one grant; identical/fresh replay rejected; expiry while blocked on a row lock;
retention across rollback/reapply; no manufactured delivery state. These are
pending CI execution at this checkpoint. A local PostgreSQL service was not
available; no Neon branch was used for mutation tests.

Activation gates: exact-head CI plus final independent I2 review; explicit owner
approval for migration 0370 and the one claim RPC; quiesce the old bridge;
fresh primary-source/ACL/checksum checks; apply migration; pin the approved
source commit in the staged prompt; update/read back the existing automation
without changing its complete trigger set; then observe a future natural event.
Do not manually replay #1683 or #1059/#1599. Their state at 21:49:39 UTC was
respectively FAILED_CLOSED and SENT/SENT, with no accepted terminal result.
Prompt readback and the natural-event canary remain outstanding.
This document does not claim successful delivery, accepted terminal results,
or a completed end-to-end autonomous cycle.
