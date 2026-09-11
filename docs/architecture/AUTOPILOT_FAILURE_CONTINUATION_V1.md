# Autopilot failure continuation v1

Status: implementation candidate.  Migration: `0323_autopilot_failure_continuation`.

## Required behavior

A terminal result belongs to one task lane; it is not a global scheduler stop.
The original result and evidence remain immutable, while the controller performs
two independent actions:

1. leave every other `READY` task claimable by the resident worker;
2. for an accepted technical `BLOCKED` role result, create one `REPAIR` task.

The resident worker already drains every eligible `READY` task.  Therefore a
failure lane and other queued work progress independently.  A pre-declared
successor may depend on the failed task, so it remains success-gated instead of
being launched blindly after failure.

## Bounded repair

`REPAIR` receives only the target pull request, exact original head, role,
origin/prior task IDs, result code, and a 160-character secret-free summary.
Its instruction is fixed to diagnose, apply a minimal change, and test it.  It
cannot merge, deploy, update refs directly, change production data, request a
second repair attempt, or invent another capability.

If the repair reports `SUCCEEDED`, the controller creates one `VERIFY` task
bound to the returned exact head.  `VERIFY` is read-only.  Successful
verification releases the original dependent successor.  A failed verification
is terminal and cannot recursively create another repair.

Owner/account/credential/payment/canon-decision blockers do not receive a fake
technical repair.  They preserve the owner boundary while already queued
independent work continues.

## Rolling deployment

The worker first asks for the v2 outbox envelope and falls back to the existing
read-only claim RPC while migration `0323` is absent.  Existing read-only
dispatch bodies are byte-for-byte unchanged.  The migration can therefore be
applied after the compatible worker and broker are live.

## Acceptance proof

The required canary is:

`BLOCKED origin -> FAILED_CLOSED evidence retained + REPAIR READY -> REPAIR SUCCEEDED -> VERIFY READY -> dependent successor READY`.

The PostgreSQL integration test also proves that a failed `VERIFY` creates no
additional repair and that an owner-only blocker creates no repair.
