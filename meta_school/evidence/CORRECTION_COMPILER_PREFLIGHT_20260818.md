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

Evidence quality distribution:
- 1450 evidence rows: `quality_status=accepted`, `confidence_class=UNKNOWN`.
- 3: `derived_partial` / `MEDIUM`.
- 2: `quarantined` / `HIGH`.
- 1: `derived_checked` / `HIGH`.
- 1: `derived_verified` / `HIGH`.
- 1: `verified` / `HIGH`.

Decision extraction state:
- 328 decisions have `action_taken.status=text_only`.
- 124 are observed-text/observed-choice records with explicit completeness metadata.
- For the 124 newer records, actor attribution is marked `unavailable_without_speaker_labels`.

## Interpretation
1. `CORRECTION_COMPILER` has no production correction rows to compile. The correct Shadow result is therefore `NO_CHANGE / NO_INPUT`, not synthetic regression data.
2. Formal bridge-correctness assessment of the 452 decisions must fail closed at this stage. Most evidence is primary-source transcript evidence but has UNKNOWN confidence, actor identity is absent, student identity is absent, and no decision is linked to a deal. These rows can be audited for structure/provenance, but must not be labeled correct/incorrect by invention.
3. The 109 decisions without evidence links are a concrete provenance gap and should be handled before they can participate in learning-memory promotion.
4. Speaker separation/teacher–student attribution improvements are a dependency for student-specific learning loops.
5. DDS-based correctness assessment requires deal/position binding; current decision rows do not have it.

## Candidate implementation launched
A fail-closed `tools/correction_compiler_shadow.py` candidate is implemented on an isolated GitHub branch. It:
- accepts only confirmed/resolved corrections;
- requires `regression_required=true`;
- requires explicit evidence records and rejects quarantined/rejected/invalid evidence;
- requires explicit target component, test reference and expected contract;
- requires teacher approval for protected methodology, methodology corrections, material corrections and high/critical corrections;
- deterministically deduplicates candidates;
- emits only `candidate` regression records;
- has no database write path.

## Next gated work
1. Run synthetic regression tests for the compiler.
2. Create a decision-assessment readiness audit that produces UNKNOWN/BLOCKED instead of invented correctness labels.
3. Repair or quarantine the 109 evidence-link gaps in Shadow/preview first.
4. Only after verified actor/deal/evidence binding exists, begin formal bridge decision evaluation.
5. `STUDENT_TRANSFER_LOOP` remains blocked from student-profile writes; its first implementation must be read-only observation.
