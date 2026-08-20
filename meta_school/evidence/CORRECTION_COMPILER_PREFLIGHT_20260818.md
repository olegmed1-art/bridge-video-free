# CORRECTION_COMPILER Shadow preflight — 2026-08-18

Status: PREFLIGHT_PASS_WITH_NO_INPUT
Mode: SHADOW
Production database: READ_ONLY observation only
Stable/canonical writes: NONE

## Evidence Gate prerequisite
The corrected controlled-onboarding Evidence Gate passed on the exact PR head and was merged before this preflight. No production Neon migration was dispatched as part of that merge.

## Production read-only inventory
Project: `bridge-school-core`
Branch observed: `production` (protected/default)
Database: `neondb`

Observed row counts:
- `decision`: 452
- `decision_assessment`: 0
- `correction_record`: 0
- `regression_case`: 0
- `regression_execution`: 0
- `evidence`: 1458
- `evidence_link`: 2702

Decision provenance coverage:
- 452/452 decisions are attached to recorded lesson interactions.
- 343/452 decisions have linked evidence records.
- 109/452 decisions have no linked evidence and all 109 belong to one recorded lesson interaction.
- 0/452 decisions currently have `actor_person_id`.
- 0/452 decisions currently have `student_id`.
- 0/452 decisions currently have `deal_id`.
- 452/452 decisions have `interaction_id`.
- 0/5 recorded lesson interactions have `primary_student_id` or `group_id`.

Evidence quality distribution:
- 1450 evidence rows: `quality_status=accepted`, `confidence_class=UNKNOWN`.
- 3: `derived_partial` / `MEDIUM`.
- 2: `quarantined` / `HIGH`.
- 1: `derived_checked` / `HIGH`.
- 1: `derived_verified` / `HIGH`.
- 1: `verified` / `HIGH`.

Decision extraction state:
- 328 decisions have `action_taken.status=text_only`.
- 109 decisions have `action_taken.status=observed_text`.
- Only 15 decisions have `action_taken.status=observed_choice`.
- All 124 newer observed-text/observed-choice records are marked `actor_attribution_status=unavailable_without_speaker_labels`.
- The 15 `observed_choice` records still contain teacher/explanatory transcript fragments in multiple sampled cases, so the status alone cannot be treated as proof that a student actually made the choice.
- Therefore 437/452 are explicitly not stored as observed choices, while the remaining 15 still lack verified actor and deal/position binding.

109-row provenance gap:
- the affected interaction has no normalized `episode.evidence_ids` and no normalized `decision.evidence_ids`;
- however its episode metadata still contains upstream `evidence_refs` such as `segment_*` identifiers;
- production `evidence.locator` stores `worker_segment_id`, which creates a plausible deterministic repair path;
- this path is not yet approved as a repair because the decision-to-segment mapping must first be proven one-to-one and reproducible. No production backfill was attempted.

## Interpretation
1. `CORRECTION_COMPILER` has no production correction rows to compile. The correct Shadow result is therefore `NO_CHANGE / NO_INPUT`, not synthetic regression data.
2. Formal bridge-correctness assessment of the 452 decisions must fail closed at this stage. Most evidence is primary-source transcript evidence but has UNKNOWN confidence, actor identity is absent, student identity is absent, and no decision is linked to a deal. These rows can be audited for structure/provenance, but must not be labeled correct/incorrect by invention.
3. The 109 decisions without normalized evidence links are a concrete provenance gap and should be handled before they can participate in learning-memory promotion.
4. Speaker separation/teacher–student attribution improvements are a dependency for student-specific learning loops.
5. DDS-based correctness assessment requires deal/position binding; current decision rows do not have it.
6. A `decision` record is currently better interpreted as a candidate decision mention unless independent evidence proves actor, choice and context. The Shadow readiness audit must enforce that distinction.

## Candidate implementations launched

### CORRECTION_COMPILER
A fail-closed `tools/correction_compiler_shadow.py` candidate is implemented on an isolated GitHub branch. It:
- accepts only confirmed/resolved corrections;
- requires `regression_required=true`;
- requires explicit evidence records and rejects quarantined/rejected/invalid evidence;
- requires explicit target component, test reference and expected contract;
- requires teacher approval for protected methodology, methodology corrections, material corrections and high/critical corrections;
- deterministically deduplicates candidates;
- emits only `candidate` regression records;
- has no database write path.

### Decision assessment readiness
A separate `tools/decision_readiness_shadow.py` candidate is implemented. It does **not** judge bridge correctness. It only checks whether a decision has enough verified evidence, actor attribution and deal/position binding to be sent to an independent formal assessor. It always leaves `correctness_label=null` and exposes explicit blocker codes.

## Current real-data Shadow conclusion
- `CORRECTION_COMPILER`: `NO_INPUT / NO_CHANGE` because `correction_record=0`.
- Formal decision assessment: `BLOCKED` for the present production corpus under fail-closed gates.
- `STUDENT_TRANSFER_LOOP`: `BLOCKED` because student identity is unresolved and formal decision assessment is not ready.
- No production data was modified.

## Next gated work
1. Validate both Shadow candidates in CI.
2. Prove or reject a deterministic one-to-one repair for the 109 missing normalized evidence links using upstream `segment_*` references and `evidence.locator.worker_segment_id`.
3. Keep any repair in preview/sandbox until regression and read-back evidence pass.
4. Feed verified speaker/role attribution into decision readiness; do not infer student identity from names or transcript wording alone.
5. Bind real bridge decisions to deal/position context before DDS or bridge-correctness evaluation.
6. Only then start independent formal assessments and the read-only `STUDENT_TRANSFER_LOOP`.
