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

A confirmed review finding required another PR update. Before any new push,
GitHub showed zero in-progress and zero queued runs for preparation workflow
ID 336379251. Only that optional compute workflow,
`.github/workflows/dds-main-30k-prepare.yml`, was reversibly paused through the
existing GitHub access. A fresh API read confirmed **disabled_manually**.
No branch protection, required CI, receipt validation or other safety gate was
disabled. The observable verifier is retained; any waiting run for this PR
must be cancelled explicitly because no preparation attestation is expected.

The workflow remains paused after this software patch. Do not re-enable it
merely because the PR merges. The DDS owner must first provide and independently
review an explicit admission gate for the exact scope and budget. Re-enabling
it is a separate compute-authority decision. Manual dispatch is also unavailable
while paused; there were no active authorized jobs when containment occurred.

PR merge cannot retrigger these preparation/verifier workflows: their PR events
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
