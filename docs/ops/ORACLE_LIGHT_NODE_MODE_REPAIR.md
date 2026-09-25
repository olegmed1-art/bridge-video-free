# Oracle Light single-directory mode repair — 2026-09-25

Scope: ASSURED recovery, issues #1946 / PR #1947. Keep Light HOLD and task queue
empty. Only remove group-write from `/home/ubuntu/.nvm/versions/node/v22.23.2`.
No installer guard change, CLI install, service restart, database write or pilot.

## Review and execution

1. Independent I2 review of this exact script/runner/workflow and passing CI
   precede merge. Reconcile main and PR head immediately before merge.
2. Run workflow `Oracle Light Node mode repair`, `mode=inspect`, with the exact
   current main SHA. It verifies the live process, same invocation, HOLD,
   read-only database login, zero nonterminal tasks, exact failure cause and
   absent CLI target. Save the numeric mode and proposed mode from its output.
3. Independent reviewer must approve that observed mode transition and the
   exact current code. The observed mode must match the input at repair time.
4. Exclude concurrent NVM maintenance on the host. The Actions concurrency group
   serializes cooperating Light operator workflows and directory flock
   serializes this helper. Neither prevents arbitrary root/ubuntu programs from
   changing metadata; these are trusted principals. This is not an adversarial
   same-UID containment mechanism and chmod is not a compare-and-swap.
5. Dispatch `mode=repair`, exact current main, and four-digit `expected_mode`.
   Fresh live checks execute again within that run, immediately before chmod.
   One root process retains every descriptor through postchecks and rollback.
6. Accept only `APPLIED_VERIFIED`, original diagnostic Node/npm SAFE, absent
   CLI target and full live HOLD/queue-zero attestation. A changed invocation,
   unknown ACL, changed path/inode/mode or unexpected diagnostic stops the run.

## Rollback

PREPARED logs the original numeric mode before attempting a write. Any failure
after that attempt restores the same held inode's mode, even if its path was
renamed/deleted. It does not chmod a replacement inode. Unknown ownership, ACL
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

16 focused tests cover real filesystem parent swaps, symlinks, renamed or
replaced targets, original-mode rollback, uncertain rollback, ACL refusal,
prewrite drift and runner HOLD/diagnostic gates. Python compilation and YAML
parsing also pass. Host execution and numeric mode remain unverified until
the separate inspection run.
