# VIDEO_BASELINE_V1 pre-implementation audit

Date: 2026-09-15  
Governance mode: `STANDARD`  
Base revision: `de0421b3a169f54c33fe4927bc8928e418ddb895`  
Status: `AUDITED / IMPLEMENTATION_NOT_STARTED`

## Decision

The next Video 3.1 increment is a portable, deterministic single-video
baseline contract. It will consume fixed, already-produced JSON fixtures and
will not invoke media tools, recognizers, servers, queues, Drive, Neon, DDS,
School Canon, or production paths.

The first vertical slice must prove stage accounting and evidence binding. It
must not claim that technical completion establishes bridge correctness,
pedagogical value, or publication readiness.

## Current execution map

| Stage family | Current implementation | Baseline treatment |
| --- | --- | --- |
| Source, audio, ASR, QC, timeline, keyframes, speakers, package | Executed by `universal_video.runner` | Represented by immutable fixture receipts only |
| Card recognition | External producer; consumer merged in PR #1125 | Consume only through `universal_video.card_recognition_consumer` |
| Bridge context | Listed by the test profile but deferred by the runner | Explicit `DEFERRED` |
| Bridge positions | Listed but no approved default pixel backend/profile | Consumer result only; never activate a backend |
| DDS3 | Optional and deferred | `NOT_RUN` |
| Educational candidates | Listed but deferred | Evidence-linked review candidates only; never Canon facts |
| Publication | Shadow-only | `BLOCKED_SHADOW_ONLY` |

The stable 3.1 FREE semantic modules (`bridge_worker_3_1_free.py` and
`run_master_3_1_free_semantic_v2.py`) are separate downstream implementations.
The baseline must not silently import them as though the Universal Video
runner had executed those stages.

## Reconciled safety boundary

The historical `VIDEO_ANALYSIS_3_1_TEST.md` describes a possible `39 -> 13`
fourth-hand derivation. The newer merged consumer contract deliberately
prohibits logical card inference and preserves every unobserved slot as
`UNKNOWN`. `VIDEO_BASELINE_V1` follows the newer fail-closed contract: it must
not derive the fourth hand, even when the deck complement is unique.

Known cards require the caller-approved immutable tuple
`recognizer_version + recognition_profile_id +
profile_verification_sha256`. Temporal evidence additionally requires one
validated stable `deal_identity`. A visual anchor is admissible only with the
caller-approved profile-bound inlier gates.

## First vertical slice

Planned new files:

- `universal_video/video_baseline_v1.py` — pure JSON composer and validator;
- `tests/test_universal_video_baseline_v1.py` — contract and negative tests;
- `tests/fixtures/video31_baseline_v1/` — fixed input, policy, and expected
  output fixtures;
- `docs/architecture/VIDEO_BASELINE_V1.md` — versioned input/output contract.

The composer will:

1. verify source, ASR-segment, frame, recognizer-result, and policy hashes;
2. call the existing card-recognition consumer instead of duplicating it;
3. require every educational review candidate to reference source-bound time,
   frame, transcript, or card evidence;
4. report technical, ASR, visual, domain, pedagogical, and publication
   readiness independently;
5. return exactly one honest content status: `FULL`, `PARTIAL`,
   `ARCHIVE_ONLY`, `REVIEW`, or `FAILED`;
6. keep `canonical_promotion_allowed=false`,
   `production_activation_allowed=false`, and
   `publication_readiness=BLOCKED_SHADOW_ONLY`;
7. emit a canonical SHA-256 receipt so the same fixtures can be replayed on
   Oracle or IBM without changing logic.

## Acceptance criteria

- deterministic byte-for-byte normalized output for the fixed fixtures;
- all referenced evidence exists in the same hash-bound input package;
- missing, duplicated, cross-source, or untrusted evidence fails closed;
- UNKNOWN card slots remain UNKNOWN and no deck-complement inference occurs;
- executed and deferred stages are disjoint;
- technical success cannot upgrade domain, pedagogical, or publication state;
- unit/contract regression is green without network or media access;
- no overlap with current open Video dispatch PRs, which contain mailbox
  evidence only, or PR #1059, which changes production publication paths.

## Explicit exclusions

- no import or runtime integration of PR #1106;
- no real video, audio, frame extraction, ASR, OCR, or pixel recognition;
- no server, Oracle, IBM compute, container, queue, production, Drive, or Neon
  mutation;
- no DDS execution;
- no Canon or School Canon write;
- no backend activation, threshold relaxation, or accuracy claim;
- no merge without a separate explicit decision after exact-head CI and
  review.

## Rollback

The audit and the planned baseline are isolated additions. Rollback is removal
of the new baseline files; no runtime route, persistent data, or external
system state is changed.
