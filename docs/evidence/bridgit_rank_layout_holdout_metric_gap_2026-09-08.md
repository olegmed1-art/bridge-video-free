# Bridgit rank-layout holdout metric decomposition gap

Date: 2026-09-08

Task: `CR-HOLDOUT-MEASUREMENT-24`

Mode: `SHADOW_ONLY`

Starting exact head: `1595f146d056b8585ea93b9f26ad4a82a0c5b817`

Status: `BLOCKED_CORPUS`

This record closes a measurement-contract ambiguity found while re-reading the frozen holdout contracts against the current `CR-HOLDOUT-MEASUREMENT-24` metric list. It does not change the detector, profile, thresholds, production registration, Canon/WORLD, Video 3.1, or any server/media path.

## Concrete finding

The current V1 score-report schema cannot represent the full metric decomposition now required by `CR-HOLDOUT-MEASUREMENT-24`.

V1 exposes:

- `gold_visible_targets`;
- `accepted_correct`;
- `accepted_wrong`;
- one combined `rejected_or_unknown` counter;
- coverage / precision / recall;
- seat errors;
- false complete deals;
- combined `per_layout_theme_metrics`.

The current task additionally requires distinct values for:

- total cards;
- accepted-correct cards;
- accepted-wrong cards;
- rejected-ambiguous cards;
- rejected-low-score cards;
- `UNKNOWN` cards;
- accepted-wrong card+seat;
- per-source results;
- per-layout results;
- and source diversity that also distinguishes theme/UI diversity.

Therefore a V1-valid report can still lose information required by the current acceptance task. In particular, `rejected_or_unknown` cannot be mechanically split after the fact unless the sealed recognizer-output bundle preserves a deterministic rejection taxonomy.

## Existing recognizer evidence that can support a taxonomy

The current shadow backend already emits fail-closed statuses/reasons rather than logically completing a deal. Relevant result classes include:

- `PARTIAL_PLAY`;
- `LAYOUT_UNKNOWN`;
- `LAYOUT_AMBIGUOUS`;
- `AMBIGUOUS`;
- `PENDING_TEMPORAL_CONSENSUS`;
- `SHADOW_FULL_LAYOUT_CANDIDATE`.

For scored weak-evidence cases it also retains threshold-facing evidence such as `minimum_assigned_score`, `minimum_required`, `minimum_rank_ink_fraction`, `minimum_rank_ink_required`, assignment uncertainties and per-frame assignment issues.

This is enough to define a metadata-only scorer without expanding detector functionality, but the mapping must be frozen before any holdout result is revealed.

## Required V2 measurement contract before honest scoring

A successor measurement surface must freeze, before holdout execution:

1. **Manifest dimensions**
   - separate `layout_family` and `theme_family` (V1 currently combines them as `layout_theme_family`);
   - source/session identity;
   - frame and decoded-pixel SHA-256;
   - source-native width/height or deterministic resolution group;
   - the existing complete/non-complete and stress tags.

2. **Sealed per-case recognizer-output envelope**
   - `case_id`;
   - exact backend/profile/head binding;
   - result `status` and `reason`;
   - every concrete emitted card+seat pair;
   - evidence fields needed to distinguish low score/ink/support from ambiguity;
   - completeness state;
   - no inferred fourth hand and no logical card promoted to `VISUAL`.

3. **Non-overlapping card-level rejection buckets**
   - `accepted_correct_card`;
   - `accepted_wrong_card`;
   - `accepted_correct_card_seat`;
   - `accepted_wrong_card_seat`;
   - `rejected_ambiguous`;
   - `rejected_low_score`;
   - `unknown`;
   - optional `rejected_other_fail_closed` only if required for conservation, never silently folded into another bucket.

4. **Conservation checks**
   - every frozen gold-visible target must belong to exactly one accepted/rejected/unknown card-level outcome;
   - card+seat counters must be recomputed independently from concrete emitted pairs;
   - no accepted result may be synthesized from ordering, colour, grouping, deck complement, fourth-hand completion, or any other logical inference.

5. **Stratification**
   - global;
   - per source session;
   - per layout family;
   - per theme family;
   - per resolution group;
   - per required stress tag;
   - complete versus non-complete.

6. **Freeze receipt**
   - recognizer exact head;
   - backend version;
   - profile/config digest;
   - thresholds;
   - manifest digest;
   - human-gold digest;
   - scorer/taxonomy version;
   - all frozen before recognizer-output reveal.

The existing thresholds remain unchanged: card+seat precision >= 0.995, card+seat recall >= 0.95, seat errors == 0, false complete deals == 0. Nothing in this record authorizes threshold tuning.

## Corpus state remains unchanged

No real frozen independent human-gold corpus was found during this cycle. The planned 24-case holdout is still not evidence. The minimum corpus remains at least 24 genuinely unseen cases from at least 4 independent source sessions, with the previously frozen complete/non-complete, resolution, layout/theme, void, partial-play, transition, blur/compression, overlap/occlusion, pointer-transition and negative-anchor/layout strata, and zero train/template/reference/development overlap by frame and decoded-pixel identities.

Therefore no accuracy number is reported here. All requested holdout metrics remain `N/A` until real evidence is frozen.

## Next safe step

Define the metadata-only V2 output/taxonomy + score-report contract and a lightweight offline scorer with synthetic **metadata fixtures only**. Synthetic fixtures may test the scorer mechanics but must never count as holdout accuracy evidence. Do not run the detector on a holdout until the real manifest, independent human gold, thresholds, config/backend/profile and scorer taxonomy are frozen.

## Safety receipt

- detector expanded: `false`;
- threshold tuning performed: `false`;
- heavy server/media run: `false`;
- production video: `false`;
- Oracle/container mutation: `false`;
- merge/deploy: `false`;
- Canon/WORLD promotion: `false`;
- PR #1125 touched: `false`;
- `canonical_promotion_allowed`: `false`.
