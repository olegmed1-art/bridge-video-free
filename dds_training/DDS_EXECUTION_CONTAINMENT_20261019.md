# DDS automatic preparation incident — 2026-10-19

Scope: PR #2186 software correction; no 30k preparation budget was approved.
Opening the PR unintentionally triggered the existing `pull_request_target`
workflow. Its triggers should have been audited before the PR was opened.
Mode: INCIDENT for containment; ASSURED remains the software-review mode.

## Completed actions and evidence

- [Preparation run 39296951764](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39296951764),
  job 118211827060: terminal **cancelled**.
- [Observable verifier 39296953065](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39296953065),
  job 118211829803: terminal **cancelled** while waiting; its evidence upload skipped.
- Preparation restored the verified existing 10k pilot from Drive, generated a
  deterministic 30k corpus (20k fresh deals / 40k fresh tasks), and locked 28,000
  TRAIN predictions. Prediction SHA-256:
  `ae946bec73f12bead7ebd6e54947bb6f2d2268798cf08b7b468495f943befe2b`.
- The pilot DB identity remained unchanged. Bootstrap was interrupted during
  the pinned DDS build with `DDS_RUN_PREFLIGHT=0`. Cross-fit/DDS preflight,
  holdout verification, trusted attestation and prepared-state packaging were
  skipped. Logs and command structure show no new DDS evaluation, validation
  or sealed-test execution. This does not erase the unapproved preparation.
- Preparation log span was approximately 17:46:16–17:50:17 UTC, about four
  minutes of runner use; monetary cost was not measured. Existing fast CI also
  runs a synthetic corpus-generation selftest; this is not real DDS evaluation.
- Compact incident artifact ID 12207958079, 578 bytes,
  `dds-main-30k-pr-evidence-39296951764`, SHA-256:
  `1886d745aa1f2b77846a9da02be072d8deeb036ef887889c62da48c9eda1c848`.
  It is an incident trace, not readiness or compute authorization.
- Cache step conclusion says success, but its log reports cache-write denial.
  No successful cache persistence is claimed. No new resource, key, permission,
  paid API call or IBM/Diana20 operation was introduced.

## Preventing repeat execution

### Read-only follow-up: exact usage and billing evidence

The GitHub run/job/timing APIs were reread after containment:

| Field | Observed value |
| --- | --- |
| Run / attempt / event | 39296951764 / 1 / pull_request_target |
| Job | 118211827060, prepare-main |
| Run duration reported by timing API | 250000 ms = 4m10s |
| Job started / completed (UTC) | 2026-10-19 17:46:15 / 17:50:20 |
| Job elapsed time | 245 seconds = 4m05s |
| Runner | GitHub-hosted standard ubuntu-24.04; GitHub Actions 1000055012 |
| Runner group | GitHub Actions, group ID 0 |
| Repository visibility | public (`private=false`) |
| Timing API billable.UBUNTU.total_ms | 0 |
| Timing API job duration_ms in billable section | 0 for job118211827060 |
| Terminal outcome | completed / cancelled |

Primary API endpoints: [run](https://api.github.com/repos/olegmed1-art/bridge-video-free/actions/runs/39296951764),
[jobs](https://api.github.com/repos/olegmed1-art/bridge-video-free/actions/runs/39296951764/jobs),
[timing](https://api.github.com/repos/olegmed1-art/bridge-video-free/actions/runs/39296951764/timing).
The timing response is positive evidence of **zero reported billable runner
milliseconds**, not a monetary invoice. It agrees with GitHub's documented
[free standard runner use in public repositories](https://docs.github.com/en/billing/concepts/product-billing/github-actions).
The account billing-usage request for 2026-10-19 returned HTTP404 and the CLI
reported missing `user` scope. No scope/credential was expanded. Actual invoice
charges and any account-level storage charges remain **NOT VERIFIED**, not zero.

Only preparation workflow336379251 is disabled_manually. The read-only snapshot
confirmed verifier339542710, DDS fast335409582 and Secret gate340767500 active.
The existing 578-byte incident artifact and job/timing receipts were downloaded
locally for preservation. No corpus, remote artifact, cache or receipt was deleted;
no run was retried. Full generated-state packaging had been skipped by cancellation,
so persistence of the generated full corpus is not asserted.

### Why PR creation triggered computation

At head b66311a the preparation workflow explicitly subscribed to
`pull_request_target` opened/synchronize/reopened, branch main and paths including
`dds_training/**`. Its job condition accepted an owner-authored same-repository PR.
PR2186 satisfied every condition. The trust check protected secret exposure but
did not check a preparation scope, budget, receipt or explicit launch approval.
The architecture selftest even allowed this exception because preparation did
not call the mass evaluator. That assumption was insufficient: corpus generation,
prediction and binary construction were still real work.

### Durable source gate

Automatic preparation triggers are removed. The preserved historical preparation
job is unconditionally quarantined with `if: ${{ false }}` before runner allocation.
Manual dispatch or administrative re-enabling alone cannot execute it. No checkbox
or budget string enables a bypass. The owner's subsequent placement decision
requires heavy DDS work on the existing OracleLight -> IBM route, not GitHub
Actions. A future reviewed dataset/queue/resource admission belongs on that route;
it must not replace this denial with GitHub computation. The administrative pause
also remains. IBM server costs are authorized and are not being audited here.

The architecture selftest rejects restoration of an automatic trigger, removal or
dynamic replacement of the denial, and addition of an unguarded job. The active
observable verifier now fails immediately with explicit BLOCKED/EVIDENCE_ONLY
evidence when preparation is disabled/quarantined or its state cannot be read.
It retains/uploads its blocked receipt; it does not report successful preparation
or poll for thirty minutes. Other software/security CI remains active.

A confirmed review finding required another PR update. Before any new push,
GitHub showed zero in-progress and zero queued runs for preparation workflow
ID 336379251. Only that optional compute workflow,
`.github/workflows/dds-main-30k-prepare.yml`, was reversibly paused through the
existing GitHub access. A fresh API read confirmed **disabled_manually**.
No branch protection, required CI, receipt validation or other safety gate was
disabled. The observable verifier is retained. Before this source correction,
its waiting runs were cancelled explicitly because no preparation attestation
was expected. After this correction it must finish with a BLOCKED receipt.

The workflow remains paused after this software patch. Do not re-enable it
because the PR merges or an IBM budget exists. Heavy DDS execution is assigned
to existing OracleLight -> IBM with dataset/scope gates and resource coordination.
Manual dispatch is also unavailable
while paused; there were no active authorized jobs when containment occurred.

The preparation workflow no longer has PR triggers. The verifier's PR events
are only opened, synchronize and reopened. Downstream Drive attestation requires
successful preparation and therefore cannot attest the cancelled run.

## Software acceptance remains separate

The original head `b66311a9ebe152cb8f1cf9f4539f0832af0880d5` passed
[45/45 fast CI invocations](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39296952974),
but its approval was superseded by an independently reproduced P2: a truncated
score map contradicted `best_defense_tricks` and falsely accepted a lead.
The fail-closed follow-up requires renewed independent review and final-head CI.
Neither software acceptance nor corpus availability promotes Learning v2.5,
proves stable skill, or authorizes a validation/sealed run.
