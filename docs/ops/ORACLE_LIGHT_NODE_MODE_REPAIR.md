# Oracle Light four-directory mode repair — 2026-09-25

Scope: ASSURED recovery, issue #1946. Keep Light HOLD and task queue empty.
Director approved expanded scope on 2026-09-25 after inventory run 36158752306
and independent I2 design review. Exactly four directories change 0775 -> 0755:
`/home/ubuntu/.nvm`, its `versions`, `versions/node`, and
`versions/node/v22.23.2` directories. No recursive changes.
No installer guard change, CLI install, service restart, database write or pilot.

## Review and execution

1. Independent I2 review of this exact script/runner/workflow and passing CI
   precede merge. Reconcile main and PR head immediately before merge.
2. Run workflow `Oracle Light Node mode repair`, `mode=inspect`, with the exact
   current main SHA. It verifies the live process, same invocation, HOLD,
   read-only database login, zero nonterminal tasks, exact failure cause and
   absent CLI target. Verify all four paths, numeric mode 0775 and proposed mode 0755 in its output.
3. Independent reviewer must approve that observed mode transition and the
   exact current code. The observed mode must match the input at repair time.
4. Exclude concurrent NVM maintenance on the host. The Actions concurrency group
   serializes cooperating Light operator workflows and directory flock
   serializes this helper. Neither prevents arbitrary root/ubuntu programs from
   changing metadata; these are trusted principals. This is not an adversarial
   same-UID containment mechanism and chmod is not a compare-and-swap.
5. Dispatch `mode=repair`, exact current main, and `expected_mode=0775`.
   Fresh live checks execute again within that run, immediately before chmod.
   One root process retains every descriptor through postchecks and rollback.
6. Accept only `APPLIED_VERIFIED`, original diagnostic Node/npm SAFE, absent
   CLI target and full live HOLD/queue-zero attestation. A changed invocation,
   unknown ACL, changed path/inode/mode or unexpected diagnostic stops the run.

## Rollback

PREPARED logs the original numeric mode and directory count before writing.
Any failure after an attempted write triggers reverse-order restoration of all
attempted held inodes, even if paths were renamed/deleted. A failed restoration
does not prevent attempts to restore the other directories. All four original
modes and identities must verify before ROLLBACK_DONE. It does not chmod a replacement inode. Unknown ownership, ACL
or mode drift blocks rollback rather than overwriting a concurrent change.
`ROLLBACK_DONE` means mode restored; it does not prove service health. The runner
rechecks live HOLD after failure and separately reports if it cannot verify it.
`ROLLBACK_UNCERTAIN`, SSH loss or missing terminal output requires read-only
reconciliation. Do not automatically rerun repair, rollback or installation.

## Following stage

CLI installation remains a separate existing workflow with fresh live
attestation and exact current-main pin. After successful installation, verify
the CLI path/version and authorization separately. Never infer authorization
from package presence. No task processing or HOLD release without another
explicit decision.

## Local verification

29 focused tests cover each of four failing chmod positions, calls that change
mode then raise, reverse rollback, replaced parents, unknown drift with continued
rollback, nonrecursive scope, symlinks, ACL refusal and live runner gates.
The four syscalls are not atomic: process/host death can leave a partial removal
of group-write. Missing or uncertain terminal output requires fresh inventory;
never infer completion or automatically retry installation.
