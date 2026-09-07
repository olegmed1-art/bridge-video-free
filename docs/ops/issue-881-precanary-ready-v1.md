# Issue 881 bounded external pre-canary gate

`PRECANARY_READY_V1` is a future, one-run approval contract. Merging the
hardening change does not approve or dispatch the external workflow. The
operator must reconcile live `main`, Issue #881, review/CI, production queue
state, Oracle state, and all infrastructure runs again before asking for a new
Director GO.

## One-shot approval receipt

Generate a unique lowercase 64-hex nonce locally. From the exact merged current
`main`, render the canonical receipt without hand-editing it:

```bash
python3 ops/issue_881_precanary_one_shot.py render \
  --exact-sha "$EXACT_MAIN" \
  --approval-nonce "$APPROVAL_NONCE" \
  --recover-container-from-run ''
```

The repository owner must post the rendered text as a fresh, unedited comment
on Issue #881. It expires in 15 minutes. Its numeric comment ID is the
`approval_receipt_id`. The nonce is the `approval_nonce` input. Recovery is a
separate fail-closed case: name the immediately preceding completed
first-attempt failed run in both the receipt and
`recover_container_from_run`, and obtain a new GO. A machine-validated linear
chain is capped at four total runs for one exact SHA; every link needs a unique
receipt and may reference only its immediate predecessor.

## Authoritative workflow and exact inputs

Only `.github/workflows/issue-881-authoritative-external-evidence.yml` is
authorized for this gate. A future dispatch must use:

```yaml
exact_sha: <exact current main after the hardening PR is merged>
director_go: true
approval_receipt_id: <fresh Issue 881 owner comment ID>
approval_nonce: <the receipt's unique 64-hex nonce>
recover_container_from_run: ""
```

GitHub's run title must become
`issue881-precanary/<exact_sha>/receipt-<approval_receipt_id>/recover-none` for
the initial run, or end in `recover-<immediate-failed-run-id>` for an approved
recovery. The workflow rejects attempts greater than one, reuse of a receipt,
a forked/skipped recovery link, more than four same-SHA runs, a changed `main`,
a stale or edited receipt, and an exact protected gate head without clean
independent review and required CI. Concurrency is held under
`oracle-instance-workload-mutation`, but the receipt/run checks—not
serialization alone—enforce one-shot execution.

## Bounded behavior and evidence

Before the host window, the owner connection must prove the exact production
project, branch, database and pristine queue: zero batches/jobs/events, NULL
maximum event ID, sequence `1/false`, and zero claimable/leased jobs. The host
then uses one exclusive workload fence. It may build and recreate the resident
container, but must not submit a video job, download source media, mutate the
queue, write to Drive, publish canonically, or deploy Vercel.

A passing artifact contains exactly one of each material receipt, in order:

- `UNIVERSAL_VIDEO_PRECANARY_ONE_SHOT ... run_attempt=1 result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_OWNER_BEFORE ... result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_INFRASTRUCTURE_EXCLUSIVE ... result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_OCI_INSTANCE_COMMAND_EXCLUSIVE ... active_remote_commands=0 result=PASS`
- the exclusive quiescent window and immutable image digest
- no-media/no-Drive synthetic and metadata-only gates
- `UNIVERSAL_VIDEO_PRECANARY_FENCED_START ... workload_fence=exclusive result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_RUNTIME ... recreated=true worker_fenced=true ... result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_OWNER ... unchanged=true result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_OWNER_RELEASE ... worker_fenced=true ... result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS ... container_target=active ...`
- `UNIVERSAL_VIDEO_PRECANARY_DB_ENQUEUE_FENCE ... final_snapshot=unchanged result=PASS`

The post-restore runtime proof executes inside the newly resident container as
its service UID/GID while that exact worker remains blocked by the exclusive
claim fence. It must read only `/run/secrets/video-queue-dsn`, identify the
production worker principal and schema/function, and return zero
claimable/leased jobs. The independent owner transaction then acquires `SHARE
NOWAIT` locks on `video_queue.batch`, `video_queue.job`, and
`video_queue.job_event` and must match the complete pre-window baseline while
that worker is still fenced. Those locks block every queue writer through the
root-only one-use host release and full resident restoration. A final snapshot
must still be unchanged before the database fence is released.

Immediately before host mutation and again before the owner release, the runner
reconciles both the complete GitHub Actions snapshot and every OCI Instance
Agent command execution visible for the exact Oracle instance. Every repository
workflow that can create such a command shares the same
`oracle-instance-workload-mutation` concurrency fence. No display-name
allowlist is used: a command left behind by any cancelled workflow must be in a
terminal OCI lifecycle. Promotion repeats the exact-instance reconciliation
before instance lifecycle, source preparation, and image promotion mutations.
The bounded admin workflow also reconciles its own command on every exit,
attempts cancellation whenever it is not terminal, and fails unless that exact
remote command becomes provably terminal.

## STOP and rollback

The host trap restores the prior source checkout and attempts the recorded
active or inactive target state of both resident services on normal failure,
`EXIT`, `INT`, or `TERM`. Recovery evidence records target and observed states
separately, so a previous failed run cannot silently replace the source-service
target with its stopped failure state. This bounded recreation requires the
resident container target to be active. The worker stays fenced until the new
container identity, runtime queue proof, and independent owner proof pass. A
same-container identity, wrong image/process ancestry, queue drift, or identity
ambiguity keeps claim-capable residents stopped and emits
`UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED`; failed later readiness also fails
the receipt and requires reconciliation before recovery.

The pre-canary never deletes `/root/.cache` or other host content to create
space. If the disk threshold is not met, it stops before the build and requires
capacity repair plus a new receipt and Director GO. Host loss, `SIGKILL`, or
`UNIVERSAL_VIDEO_PRECANARY_RESTORE_FAILED` also forbids automatic retry; first
reconcile and restore the host under a separately approved recovery gate.

The exact future authorization text is:

> Director GO — выполнить ровно один bounded external pre-canary/recreation по
> PRECANARY_READY_V1 для exact main `<sha>` с receipt `<comment-id>`. Без
> реального видео, media processing, queue mutation, Drive write и Vercel
> deploy; при любой неопределённости STOP, без rerun или повторного dispatch.

Vercel production publication and any limited E2E pilot remain separate gates
with separate future Director approvals.

Any later container promotion is also fail-closed against stale evidence. The
attested commit must be the direct parent of the current protected `main`, and
the only changed path may be the newly added, exact promotion-request JSON.
Any intervening runtime, workflow, helper, documentation, or unrelated change
requires a new bounded pre-canary receipt and evidence run.
