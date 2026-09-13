# CR-HOLDOUT-MEASUREMENT-24

Date: 2026-09-08

Recognizer PR: #1106

Starting exact head: `62432c3b2828c975a42c4f2a56f43b93ad44c38d`

Mode: `SHADOW_ONLY`

Final status: `BLOCKED_CORPUS`

## Result

No real frozen independent human-gold corpus is present on the current PR head. The repository contains a protocol, manifest schema, planning manifest, preflight contract, and score-report schema, but the planning manifest is explicitly `UNFROZEN_PLANNING_ONLY`; its four source-session slots are `UNASSIGNED`, all 24 case slots have `evidence_status=MISSING`, and no real frame SHA-256, decoded-pixel SHA-256, timestamps, independent gold labels, frozen gold digests, recognizer output bundle, evaluator result, or scored holdout report exists.

Therefore this task must not report card precision, coverage, UNKNOWN/review rate, seat errors, false complete deals, or accepted-wrong card+seat as measured values. Any numeric accuracy result at this point would be fabricated.

## Frozen measurement contract

The acceptance bundle must separately report:

- `accepted_correct_cards`: concrete emitted cards whose card identity and seat both match frozen gold;
- `accepted_wrong_cards`: concrete emitted cards whose card identity is wrong against frozen gold, regardless of seat;
- `accepted_wrong_card_seat`: concrete emitted `card + seat` pairs that do not equal frozen gold; a correct card on the wrong seat belongs here;
- `card_precision = accepted_correct_card_seat / (accepted_correct_card_seat + accepted_wrong_card_seat)`;
- `card_coverage = (accepted_correct_card_seat + accepted_wrong_card_seat) / gold_visible_card_seat_targets`;
- `unknown_or_review`: gold-visible targets for which no concrete accepted `card + seat` is emitted because the recognizer returns UNKNOWN/review/ambiguous/non-acceptance evidence;
- `unknown_review_rate = unknown_or_review / gold_visible_card_seat_targets`;
- `seat_errors`: emitted concrete card identities assigned to the wrong seat;
- `false_complete_deals`: any full-layout candidate emitted for a gold partial/transition/ambiguous case, or any full-layout candidate containing at least one wrong `card + seat`;
- categories and stratified metrics by source session, layout family, theme family, capture resolution, and stress tags.

`rejected` case-level outcomes must be counted separately from per-card UNKNOWN/review. Rejection does not become a correct card observation.

## Independence and freeze gates

Before recognizer execution:

1. The exact recognizer head and evaluated profile/config digest are frozen.
2. All thresholds/config are frozen before any holdout result is revealed.
3. Template/train/reference frame SHA-256 values are enumerated in a development exclusion set.
4. Every holdout frame SHA-256 and decoded-pixel SHA-256 is frozen.
5. Holdout frame SHA-256 and decoded-pixel identities must have zero intersection with train/template/reference/development identities.
6. Source/session identifiers, layout/theme categories, timestamps, and capture resolution are frozen.
7. Independent human gold is produced without seeing recognizer output.
8. Gold stores only directly visible `card + seat` labels and the independent completeness state. No labels may be inferred from ordering, color, grouping, deck completion, or another hand.
9. A fourth hand is never reconstructed logically.
10. Partial deals remain partial; missing cards stay UNKNOWN/review and cannot be promoted to complete by deck complement.
11. The gold bundle and manifest receive immutable SHA-256 digests before the single untuned shadow run.
12. Any case used to tune code, thresholds, templates, profile geometry, or config is permanently removed from the unseen corpus.

## Minimum real corpus required to leave BLOCKED_CORPUS

Freeze at least **24 cases from at least 4 genuinely independent unseen source sessions**, with no overlap with the development/template source.

### 12 complete visible-deal cases

- 12 independently human-labelled complete deals;
- at least 3 real capture-resolution groups;
- at least 2 real layout/theme families when available;
- at least 4 deals containing a genuine void in one or more hands;
- source-native window position/scale variation;
- at least 2 temporally distinct retained frames for every case intended to pass temporal-consensus acceptance;
- include rotation/seat-orientation diversity if the source family can present it.

### 12 non-complete / fail-closed cases

Minimum composition:

- 3 partial-play cases;
- 2 deal/frame transition cases;
- 2 blur/compression cases;
- 2 overlap/occlusion cases;
- 2 pointer-transition or stale-pointer cases;
- 1 missing/ambiguous/wrong-layout/wrong-anchor case.

The previously failed second-deal example, if recoverable without prior tuning leakage, should be frozen as an additional independent case; if it has already influenced tuning, it belongs only in development regression and a new unseen replacement is required.

## Required acceptance report

A valid result must publish immutable hashes for:

- manifest;
- human-gold bundle;
- recognizer output bundle;
- recognizer exact head;
- evaluated profile/config;
- evaluator implementation/version;
- I2 input bundle and I2 result.

It must report globally and by source/layout/theme/resolution/stress stratum:

- accepted-correct cards;
- accepted-wrong cards;
- accepted-correct card+seat;
- accepted-wrong card+seat;
- card precision;
- card coverage;
- UNKNOWN/review rate;
- rejected case count/rate;
- seat errors;
- false complete deals;
- exact complete-deal count.

Existing target gates remain:

- card+seat precision >= 99.5%;
- recall/visible-target acceptance >= 95%;
- zero seat errors;
- zero false complete deals.

A large easy stratum must not hide a failing source/layout/theme category.

## Current measured values

All holdout accuracy fields: **NOT MEASURABLE — CORPUS ABSENT**.

- accepted_correct_cards: `N/A`
- accepted_wrong_cards: `N/A`
- accepted_wrong_card_seat: `N/A`
- card_precision: `N/A`
- card_coverage: `N/A`
- unknown_review_rate: `N/A`
- seat_errors: `N/A`
- false_complete_deals: `N/A`
- evaluator_version: `N/A`
- manifest_hash: `N/A`
- gold_bundle_hash: `N/A`

The existing five manually checked development deals remain regression evidence only and MUST NOT be reused as frozen holdout accuracy evidence.

## Safety invariants

- `SHADOW_ONLY` remains mandatory.
- `canonical_promotion_allowed=false`.
- No production write or SCHOOL CANON/WORLD promotion.
- No server/media-heavy run, Oracle/container, production video, merge, deploy, or threshold tuning is authorized by this result.
- PR #1125 is out of scope.

## Next step

Acquire and independently freeze the four unseen source-session groups and fill the 24 planned cases with real frame/pixel hashes and human gold **before** any recognizer execution. Only then run the existing offline preflight, one untuned shadow evaluation, mechanical scoring, and independent I2 recomputation.
