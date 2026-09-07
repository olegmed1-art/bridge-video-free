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
separate fail-closed case: name exactly one completed first-attempt failed run
in both the receipt and `recover_container_from_run`, and obtain a new GO.

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
`issue881-precanary/<exact_sha>/receipt-<approval_receipt_id>`. The workflow
rejects attempts greater than one, reuse of the receipt, any earlier bounded
pre-canary run for the same SHA, a changed `main`, a stale or edited receipt,
and an exact protected gate head without clean independent review and required
CI. Concurrency is still held under `oracle-instance-workload-mutation`, but
the receipt/run checks—not serialization alone—enforce one-shot execution.

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
- the exclusive quiescent window and immutable image digest
- no-media/no-Drive synthetic and metadata-only gates
- `UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_RUNTIME ... recreated=true ... result=PASS`
- `UNIVERSAL_VIDEO_PRECANARY_RESTORE_PASS ... container_target=active ...`
- `UNIVERSAL_VIDEO_PRECANARY_POSTRESTORE_OWNER ... unchanged=true result=PASS`

The post-restore runtime proof executes inside the newly resident container as
its service UID/GID. It must read only `/run/secrets/video-queue-dsn`, identify
the production worker principal and schema/function, and return zero
claimable/leased jobs. The independent owner proof must match the complete
pre-window baseline byte-for-byte at the field level.

## STOP and rollback

The host trap restores the prior source checkout and the recorded active or
inactive states of resident services on normal failure, `EXIT`, `INT`, or
`TERM`. This bounded recreation requires the resident container target to be
active. A same-container identity, wrong image/process ancestry, queue drift,
identity ambiguity, or failed readiness check makes restoration fail closed.

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
