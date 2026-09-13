# Онлайн-школа / Student Model → META Component v1.0

Status: A1_READ_ONLY_GATE_CANDIDATE

## DISCOVER
Existing School DB architecture already includes immutable/append-only student profile observations and snapshots, exact profile inputs, guarded projection activation, recommendation history, and tournament identity provenance. Recommendations are explicitly not facts. Tournament-derived learning observations require explicit identity attribution/resolution.

## Risk boundary
Because identity, student records, profile projections, permissions and persistent recommendations affect people and production data, write-capable META actions are R3 by default.

Therefore initial A1 for this component is deliberately narrower than DDS/tournament/material A1:
- R0 read-only observation/QC allowed;
- isolated synthetic Candidate logic allowed outside production student records;
- no Student/Profile/Recommendation/Identity/Permission write;
- no projection activation;
- no production schema change;
- no automated pedagogical canon change.

## Metrics
- identity_provenance_coverage;
- published_input_only coverage;
- snapshot immutability;
- recommendation_fact_separation;
- projection_generation_consistency;
- student outcome denominator/coverage;
- stale/invalidated recommendation handling;
- evidence lineage completeness.

## Guardrails
- no name-only identity matching;
- no unpublished/partial analysis output in Student Model;
- recommendation is not a fact;
- no silent overwrite of history;
- no invented student outcome;
- no pedagogical recommendation promoted to School canon;
- no write based solely on META inference;
- sensitive/student data stays within authorized scope.

## Shadow matrix
S1 complete provenance/read-only QC finding -> technical recommendation.
S2 no meaningful change -> NO_CHANGE.
S3 missing identity basis -> BLOCK attribution.
S4 methodology/canon change -> OWNER_REVIEW R4.
S5 unpublished analysis input -> REJECT.
S6 stale projection policy -> REBASE_REQUIRED.
S7 conflicting observations -> CONFLICTED, no silent averaging to truth.
S8 proposed profile write -> R3 authorization required.
S9 recommendation accepted/applied/rejected history -> preserve lifecycle, never rewrite.
S10 insufficient longitudinal evidence -> RETEST/UNKNOWN, no outcome claim.

## A1 decision rule
A1 may be enabled only as READ_ONLY/ISOLATED_SYNTHETIC for META. Any persistent Student Model write remains outside A1 and requires separate R3 authorization and validation.
