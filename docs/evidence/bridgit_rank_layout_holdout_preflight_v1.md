# Bridgit rank-layout holdout preflight V1

Date: 2026-09-07

Single-verifier amendment: 2026-09-10

Change ID: `bridgit-rank-layout-holdout-preflight-v1`

Governance mode: `ASSURED` validation planning

Status: `BLOCKED_CORPUS` — preflight contract frozen; real unseen evidence not yet collected

This document defines the mechanical preflight that must pass before the frozen 24-case Bridgit rank-layout holdout is scored. It does not run the recognizer, change detector behavior, tune thresholds, activate production, or authorize promotion.

## Inputs

The preflight consumes only metadata/evidence records:

1. `bridgit_rank_layout_holdout_manifest_v1.schema.json`;
2. a concrete manifest conforming to that schema;
3. the frozen development-source exclusion list;
4. the frozen recognizer head SHA and profile SHA;
5. single-human-verified gold bundle digest(s);
6. only after the manifest and gold are sealed, the recognizer output bundle for scoring.

No raw video, server access, Drive/Neon mutation, SCHOOL CANON/WORLD write, or production credential is required by this preflight.

## Phase A — manifest integrity

The manifest must fail closed unless all checks pass:

- schema validation succeeds;
- case count is at least 24;
- `case_id` values are globally unique;
- at least 4 distinct `source_session_id` values exist;
- no `source_session_id` belongs to the frozen development-source exclusion list;
- no frame byte SHA-256 is reused across cases;
- no decoded-pixel SHA-256 is reused across cases;
- within each case, frame timestamps are unique;
- every frame identity has both byte and decoded-pixel SHA-256;
- every case carries the exact frozen recognizer head and profile digest via the manifest root;
- `gold_card_seat_pairs` contains no duplicate card, no duplicate card+seat target, and no seat with more than 13 concrete cards;
- `gold_visible_card_count == len(gold_card_seat_pairs)`;
- a gold `COMPLETE` case has exactly 52 unique cards, 13 per seat, and `full_layout_forbidden=false`;
- every non-complete/transition/ambiguous/unknown gold case has `full_layout_forbidden=true`;
- `gold_label_origin` records whether labels were transcribed directly or derived from independent source truth;
- one non-empty `gold_verifier_channel_id` is present; a second human channel is not required;
- the verifier checks or corrects gold while recognizer output remains hidden;
- `gold_sha256` is frozen before recognizer output exists.

Any failure gives `PREFLIGHT_REJECTED`; the holdout must not be scored or used for readiness claims.

## Phase B — required corpus strata

The 24-case blocker-reduction corpus must prove, from real source-native metadata rather than synthetic-only transforms:

### Complete subset

- at least 12 `COMPLETE` cases;
- at least 3 distinct resolution groups;
- at least 2 layout/theme families when source availability permits the planned V1 diversity;
- at least 4 complete cases tagged `void`;
- each complete case intended for temporal consensus has at least 2 distinct frame byte hashes, 2 distinct decoded-pixel hashes, and distinct timestamps.

### Non-complete / fail-closed subset

At least 12 cases whose union includes:

- `partial_play`: >= 3;
- `deal_transition`: >= 2;
- `blur` or `compression`: >= 2 cases total, with both phenomena represented across V1;
- `overlap`: >= 2;
- `pointer_transition`: >= 2;
- `anchor_negative`: >= 1.

A case may carry more than one stress tag, but the corpus must still contain at least 12 distinct non-complete cases. Synthetic perturbations can supplement these strata but cannot satisfy the source-diversity requirement by themselves.

If any planned stratum is absent, status remains `BLOCKED_CORPUS` and no acceptance claim is permitted.

## Phase C — train/development versus holdout disjointness

Before recognizer execution, compare the manifest against every available development/regression identity list.

Reject the holdout if any of these overlap:

- source/session identity;
- source SHA-256 where available;
- frame byte SHA-256;
- decoded-pixel SHA-256;
- previously inspected/tuned holdout case ID;
- manually copied gold label record derived from a development fixture.

If a holdout case is inspected and then influences code, thresholds, profile geometry, templates, anchors, or selection rules, that case is permanently reclassified `DEVELOPMENT_REGRESSION_ONLY` and must be replaced before the next accuracy claim.

## Phase D — threshold freeze

Before any holdout output is revealed, seal these acceptance thresholds:

- card+seat precision >= 0.995;
- card+seat recall >= 0.95;
- seat errors == 0;
- false complete deals == 0.

The recognizer head, profile SHA, manifest SHA, gold bundle SHA, threshold record and scorer version must be included in one freeze receipt.

Thresholds may not be changed after looking at holdout results while retaining the same cases as unseen evidence.

## Phase E — scoring categories

Score identity and seat together.

For each gold-visible target:

- `accepted_correct`: one concrete emitted card+seat equals gold;
- `accepted_wrong`: a concrete emitted card+seat does not equal gold;
- `rejected_or_unknown`: no concrete accepted card+seat is emitted for that visible target.

Additional counters:

- `seat_errors`: concrete card emitted on the wrong seat;
- `false_complete_deals`: a full-layout candidate is emitted for a case whose gold forbids a full layout, or a supposedly complete candidate contains any card+seat error;
- `complete_deal_exact`: gold COMPLETE and emitted full candidate is exactly 52/52 by card+seat.

Formulas:

- `coverage = (accepted_correct + accepted_wrong) / gold_visible_targets`;
- `precision = accepted_correct / (accepted_correct + accepted_wrong)`;
- `recall = accepted_correct / gold_visible_targets`;
- `unknown_rate = rejected_or_unknown / gold_visible_targets`.

When no concrete outputs are accepted in a stratum, precision is reported as undefined; it is never silently reported as 100%.

## Phase F — stratified report

The mechanical report must include global metrics and separate metrics for:

- each source session;
- each resolution group;
- each layout/theme family;
- each required stress tag;
- complete versus non-complete subsets.

No aggregate pass may hide a stratum with a seat error or false complete deal. Any such event is an immediate bounded-corpus failure even if aggregate precision/recall would otherwise pass.

## Phase G — I2 recomputation

After manifest, gold and recognizer outputs are sealed, a logically independent I2 evaluator must recompute:

- accepted_correct / accepted_wrong / rejected_or_unknown;
- precision / recall / coverage / unknown_rate;
- duplicate-card integrity;
- 13-card hand limits;
- deck uniqueness;
- completeness classification;
- seat errors;
- false complete deals;
- final threshold pass/fail.

The I2 receipt must bind the evaluator identity, frozen input bundle SHA and result SHA. The same-model implementation/review loop is not I2.

## Final states

`HOLDOUT_READY` means only that the concrete manifest/gold bundle passes all preflight checks and is safe to execute once without tuning. It does not mean the recognizer passed accuracy thresholds.

`READY_FOR_INDEPENDENT_VALIDATION` requires a sealed recognizer output bundle whose mechanical score passes the frozen thresholds and is ready for/has completed the required independent evaluation as defined by the governing holdout protocol.

`BLOCKED_CORPUS` applies whenever real unseen sources/gold are missing, a required stratum is absent, development overlap exists, freeze evidence is incomplete, or an I2 result required for the claimed stage is absent.

No state in this document authorizes production, merge, default recognizer registration, SCHOOL CANON/WORLD promotion, hidden-hand inference, or fourth-hand completion.

## Remaining non-corpus blockers

Even a passing V1 corpus does not close:

1. the lack of a genuinely independent full-card recognition channel;
2. the need for broader evidence if 24 cases are insufficient for a final statistical production claim;
3. the unresolved former failed second-deal fixture if it is not recoverable as genuinely frozen independent evidence;
4. any separate exact-head code/test regression or current-main ancestry failure.

## Minimal implementation step after checkout recovery

Implement a small offline metadata-only preflight/scorer with synthetic metadata fixtures that enforces Phases A–F and emits the existing score-report schema. It must not import or call the Bridgit detector, OpenCV, server adapters, Drive/Neon, or production code paths. This step should be a separate bounded code/test cycle after a verified local checkout is available.

## Rollback

Documentation only. Rollback is deletion or supersession of this file. No runtime state, threshold, media, server, queue, Canon/WORLD or production resource is changed by this record.
