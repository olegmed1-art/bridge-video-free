# Fixed Oracle Light incident recovery, 2026-09-28

Purpose: finish the existing AFTER restore for scope c0795e9e…; no permission
session, worker restart, HOLD release, or pilot. Oleg delegated technical boundary
choices explicitly at 13:37–13:38 Asia/Yerevan. The procedure therefore uses its
own narrowly reviewed incident authority, without impersonating StageRunBinding.

## Compatibility and effects

Historical main, source bundle, driver, manifest, request01112f45, two original
units and journal scope remain source8bbc. The exact accepted execution commit
is separately authenticated on branch recovery/light-after-incident-20260928.
The registered stages workflow is changed only on that branch. No merge is needed.
The old consumed request is evidence only; it is never claimed again.

Canonical authorization supplied in the manual dispatch binds owner, explicit
exclusive coverage, both code identities, fixed scope/head/pair, nonce, and a
nonrenewing <=30-minute interval. Actual run/attempt/job, exact workflow bytes,
owner+triggering owner, successful contract job and current historical main are
checked independently on runner and host. Attempt 2 is refused.

Before the first journal effect: verify the private OCI backup, exact retained
request/assets/prior units/head; host rechecks these local bytes under existing
root+operation+pause locks, verifies full HOLD and AFTER and all drains. Create
one exclusive incident claim at the fixed incident-purpose SHA filename in the
existing claims directory, preserving all original claims. The receipt binds
the historical scope to the actual execution commit/run/PID1 supervisor. Its
create-only off-VM write/readback must be acknowledged before journal changes.
An existing local or remote incident claim refuses any automatic second attempt.

Use original publish_reconciled_session_suffix to advance only the already
consumed session evidence via CAS. Append/checkpoint RESTORE_INTENT, then use
original WorkflowPause and WorkflowAPI to restore workflow343949665 once.
ENABLE_INTENT is privately checkpointed before PUT. Confirm active row, append
and checkpoint ENABLED and RESTORED, compare final private remote/local pairs.
No SQL mutation is exposed: original owner sessions have read-only defaults and
snapshot transactions enforce read-only. The workflow has manual/path-filtered
push triggers, no schedule; enabling alone does not dispatch it.

## Failure and recovery

Any unknown claim/OCI CAS/PUT/ACK is terminal. No retry or rollback by guessing.
Original journal bytes are append-only. A partial result remains inspectable
with explicit phase/effect-possible codes and durable receipts. A separate
continuation would require reading the exact new local/remote state; this script
is intentionally not a generic resume command. Never delete claims to rerun it.

Host lifespan: SIGALRM540s; PID1 RuntimeMaxSec600s, KillMode=control-group,
Restart=no, NoNewPrivileges=yes, LimitCORE=0; wrapper608s and owned cleanup only.
Runner clock900s; RPC clock700s; workflow20min. These are fixed at construction,
not extended. READY/START requires >=630s remaining RPC and runner capacity after
reserving60s for completion, before any claim. No app service is stopped/restarted.
The existing historical 140s profile remains unchanged.

## Verification

17 offline tests currently PASS: real journal/checkpoint state machine with fake
external APIs, ambiguous enable/CAS/receipt failures, exact authority/identity,
immutable clocks, bounded supervised command, and isolated generated bootstrap
refusal without secret output. Offline tests do not establish production success.
A separate I2 review and CI on the exact execution tree precede live dispatch.
After success verify live registry, final checkpoint/receipt and absence of the
incident unit/cgroup, with unchanged worker PID/InvocationID/NRestarts and AFTER.
