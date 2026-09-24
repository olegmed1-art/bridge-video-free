# Light admission after completed P0 canary

Classification: ASSURED. The director requested continuation after the bounded
recovery completed. The original task and target pin are immutable; this operation
must never requeue, resend, restore or recreate the completed canary.

The installed revision remains `5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c`, exact
augmented bundle `3f7736a4fa7a6796eb9fa7be10e1d01e6cd04cb803d426991c942a91f7eb1c07`.
No new worker release, credentials, grants, database route or automation edit is
part of this operation. The old recovery attestation correctly rejects DONE;
never weaken that FAILED_CLOSED recovery gate to reuse it for a different stage.

## Freshness and inventory

Before dispatch, reconcile current main, target1769 head, original DONE task,
CALLBACK_ACCEPTED outbox, DONE work, one send intent, genuine ACK and terminal,
no follow-up, empty active task/outbox queues and zero READY/BLOCKED/ACTIVE work.
Retain digests of task/work/outbox/intent/receipt and all six paused/dependent work
items; compare after startup. Recheck live paused-reconciliation consequences of
the now CLOSED CODEX circuit. No paused work is implicitly authorized for recovery.

The installed manifest already has a durable receipt. Its registration RPC returns
the existing receipt immediately; `registered_count` is historical, not the number
created by replay. The planner claims only READY/BLOCKED work, not PAUSED or
WAITING_DEPENDENCY. These properties must be reconciled with live SQL definitions
and live inventory, not inferred from a zero task count alone.

## Administrative path

Run `.github/workflows/oracle-light-resume.yml` on reviewed exact current main,
first `preflight`, then `activate` after fresh independent database readback.
The host verifies root/host identity, SSH fingerprint, immutable installed files,
unit and ordered environment files, broker pins, service identity, route lock inode
and Neon epoch0. The read-only probe executes reviewed administrative source as
the restricted service UID against installed imports, requiring the original
canary DONE with attempts1/epoch1, zero cost/leases, no live tasks, zero capacity
reservations, exact retired-fence digest, manifest receipt and broker health.
The immutable installed recovery probe is not modified.

Activation performs another probe and main/configuration CAS under the existing
route lock before atomically changing only HOLD to ACTIVE in the existing drop-in.
It starts only Light and requires a new stable invocation with no restarts, exact
process environment/cwd, clean startup journal and manifest replay, then repeats
the read-only probe and protected-file checks after a bounded 40-second window.
Any caught failure after the mutation boundary stops Light and restores the exact
HOLD drop-in. Unexpected drop-in drift is never overwritten. SIGTERM/SIGHUP enter
this rollback path. Hard host failure cannot execute an in-process rollback.

Startup verification does not certify future dispatches or remove their existing
protected-send rules. It also does not migrate the database or unpause old work.
Independent production readback must confirm completed canary and paused-work
digests unchanged, no new task/outbox/intent, and ordinary planner heartbeat.

## Checks

Focused tests cover completed-only queue validation, competing work/capacity,
exact installed bundle, one successful start, and injected faults before mutation,
after atomic replacement, reload, start, soak, journal, post-probe and final main
verification. Every injected post-mutation failure must return to stopped HOLD.
Live PostgreSQL eligibility and post-start state checks provide independent
engine evidence; host service/process/journal evidence is separate from unit mocks.

Independent PostgreSQL rehearsal on existing child `br-holy-term-b1k8s9qe` passed
at 03:07 UTC. All three claim lanes and stale-task/callback reconciliation ran
inside a rollback subtransaction. The child's old BLOCKED canary first produced
the expected eligibility rejection; an incomplete DONE fixture was also rejected
by the terminal-shape constraint. With DONE plus completed_at simulated only
inside the rollback scope, no claim lane returned work. Full task/work/outbox/
planner snapshots matched after rollback, including timestamps. This synthetic
fixture is not production terminal evidence. All 81 focused local tests pass.
