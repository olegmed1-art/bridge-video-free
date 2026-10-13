# Completed-video staging handoff — local candidate

Date: 2026-10-13. Governance: ASSURED / I2. Local preparation only.
Baseline: PR2152 `040518f40b1298a3b84dff2b271a11690c52f927`;
main `d49feb4cb333b041928a5d37efb56a9f97dd1822`.

## Confirmed runtime gap

`bridge_neon_persistence.persist_completed_drive_job` selects an AI_DONE/master,
writes the video result, then opens separate source-identity, outbox publication
and checkpoint operations. No runtime caller hands the actual result object to
Diana. `diana_longitudinal_postprocess_v4_2.main(persisted_result=None)` separately
discovers the newest completed artifact. Its staging writer therefore lacks the
exact result by default; passing IDs alone would not bind the two selected
generations. Neither a technical completion receipt nor matching hashes grants
school/teacher access, source authorization, identity or canon acceptance.

## Minimal local composition

`database.video_staging_handoff.handoff_completed_video` has no installed runtime
caller, CLI, environment opt-in or DSN discovery. The uninstalled local
`video_operator_staging.OperatorStagingTransport` supplies the concrete operator
path described in `video_operator_staging_local.md`. The
default is BLOCKED before IO. The explicit transport is trusted server code;
passing a generated artifact/receipt dictionary cannot install it.

The transport supplies one immutable master/AI_DONE/selection snapshot plus
out-of-band generation pins and the existing local evidence reader. It is
responsible for independently verifying the report bytes and embedded-master
relation. The seam checks job, revision, source, report/master hash bindings and
completion, then runs the existing replay-input and Diana quality builders on
those same master bytes. It accepts no independently supplied quality JSON,
candidate list or persisted IDs. It installs no DDS executor, correction-store
resolver, board parser or ASR engine.

On an exclusively owned idle connection, one outer transaction composes existing
`persist_video_result_on_connection`,
`ensure_drive_source_identity_on_connection`, and
`persist_quality_candidates_on_connection`. The candidate writer receives the
actual in-memory result object and retains its independent database lineage
checks. Non-PERSISTED staging aborts the transaction, including result/identity/
technical event/outbox rows. This seam does not publish outbox rows or checkpoint
a separate job. It changes no existing writer SQL. The transport verifies the
actual worker session inside the outer transaction, before and after writers.

Only successful transaction exit and confirmed writer close yield
COMMITTED_STAGING with candidate/replay IDs. This technical receipt retains the
replay's actual BLOCKED/PARTIAL/STAGING_READY status. Exceptions after writer
entry return BLOCKED / UNKNOWN without usable IDs or backend details; reconcile
the original generation before retrying. No automatic retry or recovery policy
is installed. Exact repeats still call existing idempotent writers.

## Atomic-admission coordination

Reference only: sibling commit
`5541bfbe598a4ee271f501db294a0cc8fdf60767`,
`universal_video/admission_client.py`, `admission_binding.py`, and
`docs/architecture/fixed_subset_atomic_admission_design.md`.
The parent reports its SQL run failed with42702 before functional cases; it is
not a production-approved contract. The local successor was also launch-blocked
pending the sibling's serial harness repair. No migration0060 is copied or edited.

The published interface calls
`admit_approved_subset(contract, expected_contract_sha256, approval_id,
observation_id, database_url)` or immutable recovery with the original request.
Its client returns ADMITTED/RECOVERED_IMMUTABLE_BINDING only after its own commit.
The binding carries approval/request/contract/manifest/plan/subset hashes, actual
batch ID and per-source actual job UUID/sequence/identity/fingerprint. Queue job
UUIDs are distinct from the producer's stable job key. No mapping is guessed.

**That receipt authorizes no staging writes:** its disabled flags, including
`database_persistence_allowed=false`, remain unchanged. It is not a substitute
for an authorized source/result/candidate recording connection. Admission and
attempt freshness/lease fencing belong to the sibling contract; do not call its
connection-owning admission client inside this handoff transaction. Any future
queue wiring must reconcile its approved interface, exact source revision,
stable-job/queue-job mapping and existing staging-write authority. The original
operator path does not consume this queue and need not wait for it. Universal
Video remains SHADOW_REVIEW_ONLY; no worker hook or permission flag is changed.

## Read boundary and remaining blockers

After writer close, the existing internal service route reads candidates under
fixed-school/API-token authorization in a read-only repeatable-read snapshot.
That common-school lane differs from the pure personal teacher reader, which
requires independently authenticated school/teacher access, current revocation
and an exact excerpt disclosure binding. Handoff creates none of these facts.
The current result writer may leave a run `running`; this seam does not change
it to `success` to satisfy the personal teacher reader. Internal source
observation and personal teacher delivery therefore have different readiness.

The earlier local deliverable's tests use the real replay producer, byte reader, candidate validators and
pure teacher projection, with explicitly mocked transport/transaction/storage
and teacher authorization. They exercise missing/wrong hashes and identity,
incomplete processing, missing/tampered frames, unknown roles, repeat equality,
rollback/commit/close failures, wrong writer bindings, active connection refusal,
cross-school denial, non-teacher/revoked access, missing disclosure and incomplete
run status. They prohibit network and database connections.

Those mock handoff tests remain in the
[local evidence bundle](https://chatgpt.com/api/library/files/libfile_0cfa1812b5008191a8613561fcdc6b21/download),
and are not included in this isolated rehearsal branch. Its test command is:

```text
python -m pytest -q tests/test_video_operator_postgres.py
```

This command deliberately skips without the explicit ephemeral CI context; use
only the reviewed one-shot workflow to execute SQL. In the earlier local test
bundle, optional `BRIDGE_VIDEO_REAL_FIXTURE_DIR` points to the already authorized
private retained-artifact bundle with `MANIFEST.json`, `inputs/`, and the pinned
local evidence index. Its manifest/file hashes are checked before use. That
test proves retained-master compatibility through the same producer and a mock
writer; it does not establish original-media accuracy, independently reverify
the omitted PDF, authenticate a teacher, or prove PostgreSQL commit/fencing.
No private transcript, frame, Drive ID or credential belongs in this patch.

The earlier requirement to first build a new principal/school/generation grant
and atomic cancellation fence is superseded for the existing operator path by
`video_operator_staging_local.md`. No existing permission/evidence gate is removed.
Next safe step: review the concrete operator transport and authorize an isolated
SQL rehearsal before any production pilot. A separately authorized real pilot must
prove one exact generation's producer→committed records→independent internal
read, plus refusal cases. Local PASS does not grant production readiness.
Rollback for this uninstalled candidate is omitting its files and caller wiring.
No data rollback, release, publication, CI or video run occurs in this stage.
