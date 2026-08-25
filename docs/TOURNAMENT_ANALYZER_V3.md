# Tournament Analyzer v3

## Purpose

`bridge_school_api.tournament_analyzer_v3` is a tournament-intelligence layer above source ingestion, DDS3, BEN and the school L1 canonical runtime. It does not replace or redefine those engines.

The central rule is evidence separation: a mathematical DDS3 result, a canonical school rule, a model opinion and a teacher-review request are different evidence classes and must never be silently promoted into one another.

## Pipeline

1. Source ingestion preserves event/session/board identity and field provenance.
2. Integrity gate requires exactly N/E/S/W, 52 unique valid cards, a positive board number and event+session scope.
3. Contract-level DDS3 baseline records double-dummy opportunity and score/tournament impact without attributing a player error by itself.
4. Auction findings may attach school L1 `SYSTEM_RULE` evidence and independent BEN `MODEL_OPINION` evidence.
5. Card-by-card play analysis is permitted only when a play record exists. If absent, the play layer is explicitly `NOT_OBSERVABLE`.
6. Findings keep three independent impact dimensions: `trick_loss`, `score_loss`, `tournament_impact`.
7. Ranking prioritizes tournament impact, then score loss, then trick loss.
8. Category aggregation uses absolute losses so opposite signs cannot hide repeated problems.
9. Student output is deliberately compact. Teacher output preserves evidence classes and observability.

## Evidence classes

- `FACT`: directly present in the source.
- `DDS_FACT`: deterministic DDS3 result. This is not automatically a pedagogical/player-error attribution.
- `SYSTEM_RULE`: a matched rule from the approved school canon.
- `MODEL_OPINION`: BEN or another model recommendation/opinion with confidence and provenance.
- `TEACHER_REVIEW`: evidence is insufficient for automatic pedagogical attribution.

## Fail-closed boundaries

- A bare board number is never a globally unique identity; event and session are required.
- Missing cards are not reconstructed by this module.
- Missing play record does not permit first-swing, card-error or defense-error attribution.
- BEN is not allowed to define the school bidding system.
- DDS3 is not allowed to define teaching methodology.
- Unknown findings referencing a deal outside the ingested tournament are rejected.

## Integration contract

Upstream adapters should convert source records into `TournamentDeal`. Existing DDS3 code should provide numerical baseline values and provenance such as pinned engine/version, validated input and `fallback_used=false`. L1 and BEN callers should attach their output with `attach_system_rule()` and `attach_model_opinion()` respectively rather than overwriting the finding class.

The module intentionally leaves source-specific MP/IMP field-normalization to adapters. `tournament_impact` is a normalized value supplied by the tournament scoring adapter so the same intelligence layer can serve pairs and teams events.
