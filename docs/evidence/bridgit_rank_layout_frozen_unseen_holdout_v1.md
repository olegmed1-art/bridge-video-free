# Bridgit rank-layout frozen unseen holdout V1

Date: 2026-09-07

Change ID: `bridgit-rank-layout-frozen-unseen-holdout-v1`

Governance mode: `ASSURED` validation planning

Status: `BLOCKED` — protocol frozen; unseen evidence not yet collected or scored

Recognizer baseline for this protocol: `336553ab14fe9cf797da432e6eff61f31c1d255d`

## Purpose

This document defines the minimum frozen unseen evaluation protocol that can materially reduce the current evidence blockers for the Bridgit rank-layout shadow recognizer without expanding detector functionality or activating production.

It does not claim accuracy, production readiness, independent full-card recognition, or I2 assurance. The five manually checked development deals remain regression evidence only and are excluded from holdout metrics.

The existing shadow invariants remain mandatory: opt-in only, `SHADOW_ONLY`, no default registration, no hidden-hand or fourth-hand inference, no SCHOOL CANON/WORLD write, and `canonical_promotion_allowed=false`.

## Evidence classes

### DEVELOPMENT_REGRESSION_ONLY

An input belongs here if it was used while tuning code, templates, thresholds, anchors, profile geometry, scoring, or fail-closed behavior.

This includes:

- the five manually checked deals already used during development;
- synthetic registration transforms and theme inversion;
- erased-glyph, deal-transition, blank-frame and other developer-created fail-closed fixtures;
- sources inspected before the holdout was frozen;
- any holdout case inspected after a failure and then used to change detector/profile behavior.

Development-regression inputs may protect behavior against regressions but MUST NOT contribute to reported holdout precision, recall, coverage, seat-error, or false-complete metrics.

### FROZEN_UNSEEN_ACCURACY_EVIDENCE

A case may enter this class only when all of the following are true before recognizer execution:

1. Its source/session has not been used to tune recognizer code, templates, thresholds, anchors, or the evaluated profile.
2. Source/frame bytes and decoded-pixel identities are hash-bound.
3. Gold labels are frozen independently of recognizer output.
4. The case manifest is complete before scoring.
5. The recognizer head and profile digest are frozen before scoring.
6. No code/profile/threshold change is made after viewing the case while keeping the case in the unseen set.

If a case influences tuning, it is permanently demoted to `DEVELOPMENT_REGRESSION_ONLY`, and a new unseen case must replace it.

## Minimum V1 corpus

Freeze at least **24 evaluation cases from at least 4 independent source sessions**, with no overlap with development sources.

### Twelve complete deals

The complete-deal subset must contain:

- at least 3 capture-resolution groups;
- at least 2 layout/theme families if real sources make them available;
- at least 4 deals with a genuine void somewhere in the four hands;
- source-native scale/window-position variation, not only synthetic transforms;
- at least 2 temporally distinct retained observation frames for each case intended to qualify for temporal consensus.

A complete case qualifies as `complete_deal_exact` only when the recognizer emits a full candidate with exactly 52 correct `card + seat` pairs against frozen gold.

### Twelve fail-closed / non-complete cases

The non-complete subset must contain at least:

- 3 partial-play cases;
- 2 frame/deal-transition cases;
- 2 blur/compression cases;
- 2 overlap/occlusion cases;
- 2 pointer-transition or stale-pointer cases;
- 1 missing, ambiguous, wrong-layout, or wrong-anchor case.

A non-complete case is safe only when the recognizer does not emit a false complete deal. Appropriate outcomes may include `UNKNOWN`, `PARTIAL_PLAY`, `LAYOUT_UNKNOWN`, `LAYOUT_AMBIGUOUS`, `AMBIGUOUS`, `NEEDS_REVIEW`, or another non-complete state consistent with frozen gold.

Synthetic perturbations may remain as a secondary stress set but do not replace source-native unseen diversity.

## Frozen manifest contract

Each case must record, before recognizer execution:

- `corpus_version`;
- immutable `case_id`;
- pseudonymous source/session ID;
- source SHA-256 when source bytes are retained in the validation contour;
- every retained frame byte SHA-256;
- every retained frame decoded-pixel SHA-256;
- `timestamp_ms`;
- capture width and height;
- layout/theme/profile family;
- recognizer head SHA;
- evaluated profile digest;
- stress tags as applicable: `complete`, `void`, `blur`, `compression`, `overlap`, `partial_play`, `deal_transition`, `pointer_transition`, `anchor_negative`;
- gold visible-card count;
- exact visible `card + seat` pairs;
- expected completeness state;
- whether a full-layout result is forbidden;
- independent gold author/reviewer channel identifiers;
- frozen gold digest.

Participant names, faces, private source paths, Drive IDs, credentials, or other personal/private identifiers must not be stored in the repository evidence record.

## Freeze procedure

For every corpus version:

1. Select source sessions without consulting recognizer output.
2. Assign pseudonymous session/case IDs.
3. Freeze source/frame identity hashes and metadata.
4. Produce gold labels independently of recognizer output.
5. Freeze and hash the gold bundle.
6. Freeze recognizer head and profile digest.
7. Verify required strata are present.
8. Run the recognizer once without tuning.
9. Store raw recognizer outputs separately from gold.
10. Score mechanically against frozen gold.
11. Run independent I2 verification on the frozen gold/output bundle.
12. Only after the complete scored report is sealed may failures be inspected for tuning; any such tuned cases then leave the unseen class for all future accuracy claims.

## Metric definitions

Card identity and seat are scored together.

- `accepted_correct`: emitted concrete `card + seat` equals frozen gold.
- `accepted_wrong`: emitted concrete `card + seat` does not equal frozen gold.
- A correct card assigned to the wrong seat counts as `accepted_wrong` and as a `seat_error`.
- `unknown`: a gold-visible target for which no concrete accepted `card + seat` is emitted.
- `coverage = (accepted_correct + accepted_wrong) / gold_visible_targets`.
- `precision = accepted_correct / (accepted_correct + accepted_wrong)`.
- `recall = accepted_correct / gold_visible_targets`.
- `seat_errors`: concrete accepted outputs assigned to the wrong seat.
- `false_complete_deals`: a full-layout candidate emitted when frozen gold is partial/transition/ambiguous, or a full candidate containing any `card + seat` error.
- `complete_deal_exact`: a true complete-gold case with an emitted 52-card candidate that is exactly 52/52 correct by `card + seat`.

Metrics must be reported globally and separately by source session, resolution group, layout/theme family, and every stress tag. A large easy stratum must not hide a failing hard stratum.

When there are zero accepted concrete outputs in a stratum, precision is reported as undefined rather than silently treated as 100%; coverage and recall remain measurable.

## V1 acceptance thresholds

The existing target remains:

- precision at least **99.5%**;
- recall at least **95%**;
- **zero seat errors**;
- **zero false complete deals**.

The 24-case V1 is a blocker-reduction corpus, not automatically a sufficient final statistical corpus. Passing V1 may justify `READY_FOR_INDEPENDENT_VALIDATION` only for this bounded corpus/protocol; it does not by itself authorize production or close the independent-channel blocker.

V1 is `BLOCKED` if any of the following occurs:

- a seat error;
- a false complete deal;
- precision below 99.5%;
- recall below 95%;
- a required stratum is absent;
- a case was previously tuned against;
- gold changed after recognizer output was seen;
- required source/frame/pixel/profile/head identity evidence is missing;
- independent I2 recomputation is absent.

## Independent I2 protocol

The same-model implementation/review loop is not I2.

The minimum I2 step is:

1. Freeze the complete manifest, gold bundle, recognizer output bundle, recognizer head, and profile digest.
2. Compute a bundle digest before independent evaluation.
3. Give the frozen bundle to a logically independent evaluator: a different model, independent algorithm/formal checker, or equivalent I2 channel.
4. Recompute `card + seat` metrics, duplicate-card integrity, 13-card hand limits, deck integrity, completeness classification, seat errors, false complete deals, and threshold pass/fail.
5. Record evaluator identity, bundle digest, result digest, and discrepancies.
6. Do not allow the I2 evaluator to change recognizer thresholds, profile, or gold.

This reduces the assurance blocker but does not substitute for a genuinely independent full-card recognition channel.

## Mechanical score-report schema

The sealed result should contain at least:

```text
corpus_version
manifest_sha256
gold_bundle_sha256
recognizer_output_bundle_sha256
recognizer_head_sha256
profile_sha256
case_count
source_session_count
gold_visible_targets
accepted_correct
accepted_wrong
unknown
coverage
precision
recall
seat_errors
false_complete_deals
complete_deal_exact_count
per_source_metrics
per_resolution_metrics
per_layout_theme_metrics
per_stress_tag_metrics
i2_evaluator_id
i2_input_bundle_sha256
i2_result_sha256
i2_discrepancy_count
final_status
```

`final_status` for this protocol is one of `BLOCKED` or `READY_FOR_INDEPENDENT_VALIDATION`. It is never a production-activation signal.

## Evidence checklist

- [x] Development-regression evidence separated from unseen accuracy evidence.
- [x] Frozen-unseen admission and demotion rules specified.
- [x] Minimum V1 corpus specified as 24 cases / at least 4 independent source sessions.
- [x] Required resolution/layout/theme/void/blur/compression/overlap/partial/pointer-transition coverage specified.
- [x] Metrics and zero-error conditions defined.
- [x] I2 recomputation protocol defined.
- [ ] 24-case manifest frozen and hash-bound.
- [ ] Independent gold frozen before recognizer execution.
- [ ] Required strata proven present in manifest.
- [ ] V1 recognizer run completed without tuning.
- [ ] Mechanical global and stratified score report sealed.
- [ ] Precision >= 99.5% demonstrated.
- [ ] Recall >= 95% demonstrated.
- [ ] Zero seat errors demonstrated.
- [ ] Zero false complete deals demonstrated.
- [ ] Independent I2 recomputation recorded with zero unresolved discrepancies.
- [ ] Independent full-card recognition channel exists.
- [ ] Broader corpus sufficiency established for any later production decision.

## Current blockers

1. No genuinely independent full-card recognition channel.
2. No frozen unseen multi-source/multi-layout gold corpus has yet been collected and scored.
3. Real source-native evidence remains insufficient across resolutions, themes/layouts, voids, blur/compression, overlap/occlusion, partial play, and transitions.
4. I2 assurance has not yet been recorded.
5. Current branch CI also has a known recognizer test-regression after the earlier source-pixel replay rejection was moved earlier in the path; this protocol does not change detector code or repair that test.

## Rollback

This file is documentation only. It activates no runtime path, changes no thresholds, reads no production media, creates no external obligation, and writes no SCHOOL CANON/WORLD data. Rollback is removal or supersession of this document.
