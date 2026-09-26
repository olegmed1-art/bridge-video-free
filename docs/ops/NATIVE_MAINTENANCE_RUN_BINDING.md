# Authenticated run and job binding

2026-09-26, ASSURED preparation. A dedicated read-only main workflow acquires
`oracle-instance-workload-mutation` at workflow level and
`oracle-light-backup-mutation` at job level. Both use `cancel-in-progress: false`
and `queue: max` so this workflow queues instead of replacing an older pending
run. This briefly occupies the coordination groups; it has no SSH/Neon secret,
database mutation, service operation or task admission step.

`RunBinding.assert_running()` authenticates GitHub REST reads with the job's
read-only token and verifies the run ID, exact attempt, repository, main branch,
source commit, event, owner account ID/login and triggering actor. It fetches the
workflow bytes at that source and compares them with the independently pinned
digest of the reviewed workflow. Current main must still match. The exact-attempt
jobs endpoint must contain precisely the sole named job, executing at the same
source and attempt; its first authenticated numeric ID is pinned thereafter.
That observed job identity is not an approval of a database change.

The workflow permits owner main pushes and owner dispatch with exact main input.
The script checks local workflow/ref/job context as well, but environment fields
alone cannot authorize a principal. REST failures, redirects, incomplete job sets,
duplicate JSON fields, changed source/actor/attempt/job/status and expired validity
all refuse. Any observation failure permanently latches the instance; later good
observations cannot erase it. Response sizes and network inactivity timeouts are
bounded. The 60-second validity check is not an independent process watchdog.

The real main job performs two authenticated observations. Unit tests inject
cancellation, changed attempts, source/actor drift, ambiguous jobs, network loss
and failed-then-recovered observations. PR validation runs without acquiring the
production coordination groups; actual group-entry evidence is collected only
by the dedicated main workflow after reviewed merge.

## What this proves and what remains

Exact reviewed workflow bytes plus an executing job bind the observed job to its
declared groups. An `in_progress` status on an arbitrary job alone would not.
REST observations are not atomic with a subsequent commit and can lag changes.
Cancellation can release GitHub groups before a remote process has stopped.
Other workflows using default single-slot queue behavior can also affect pending
runs; this change does not rewrite their policies.

This class deliberately has no `assert_held(target, operation)` method and cannot
stand in for the production maintenance guard. Still required: approved DB
manifest and HOLD identity, actual scoped coordination of relevant privileged
writers (including direct owner tools and ungrouped critical paths), authenticated
controller continuity, owner-capable runtime and ambiguous-commit reconciliation.
The coordination window must survive cancellation until remote completion or
reconciliation is confirmed. No direct-owner agreement is inferred from an API
status, workflow hash, or generic autonomy instruction.

Trusted source/caller and GitHub authentication are part of the boundary. This
module does not protect against malicious privileged Python code replacing its
own guard. The existing fail-closed DB entrypoint remains unchanged.

Source for current queue semantics:
https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency

Rollback: revert the source/workflow change. The proof acquires groups only for
its bounded job, introduces no permanent service or database state, and requires
no privilege rollback.
