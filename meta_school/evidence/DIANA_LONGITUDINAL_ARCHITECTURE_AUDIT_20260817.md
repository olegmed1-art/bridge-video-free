# Diana Longitudinal Architecture — Safety/Completeness Audit
Date: 2026-08-17
Architecture: v1.0

## Coverage audit
Canon extraction/evolution/genealogy: PASS
Curriculum ordering/prerequisites/coverage/bottlenecks: PASS
Student trajectory/opportunity/retention/generalization/transfer: PASS
Teacher interventions/explanation library: PASS
Evidence/timestamps/provenance: PASS
Outcome linking: PASS
Decision timeline: PASS
Misconception graph: PASS
Knowledge conflicts: PASS
Help/independence: PASS with scalar index explicitly experimental
Forgetting/recovery: PASS with NOT_OBSERVED protection
Confidence/correctness: PASS with no tone-based psychological inference
Latency: PASS with boundary reliability requirement
Insight/latent learning/regression: PASS with causality safeguards
Second-order habits: PASS descriptive only
FAQ/error/contrast/minimal-change libraries: PASS
Generated diagnostic exercises: PASS with canon/DDS authority separation
Teaching-effectiveness analysis: PASS as hypothesis, not causal proof
Digital Student Model predictions: PASS read-only/pre-registered
Teacher briefing: PASS proposal-only
Learning debt: PASS non-judgmental state
Multi-student future generalization: PASS requires validation
Privacy/reuse: PASS
Large-video duplication rule: PASS
Terminology: School-facing Russian uses «торговля».

## Vulnerabilities identified and resolved in architecture
1. Risk: repeated teacher statement becomes canon automatically. Resolution: repeated video evidence only CANON_CANDIDATE; verified written canon/owner authority required.
2. Risk: absence of skill opportunity interpreted as forgetting. Resolution: NOT_OBSERVED distinct from FAILED and opportunity denominator required.
3. Risk: correct answer after help counted as mastery. Resolution: learning vs assessment and help sequence stored separately.
4. Risk: psychological inference from video tone. Resolution: confidence only explicit/reliably elicited.
5. Risk: causal claims about teaching from one student. Resolution: effectiveness outputs are hypotheses; Diana not universal truth.
6. Risk: DDS hidden-information optimum treated as human-error proof. Resolution: mathematical and information-available judgments separated.
7. Risk: generated exercises contaminate historical evidence. Resolution: generated artifacts explicitly Candidate/derived.
8. Risk: Student Model prediction becomes fact. Resolution: prediction frozen before outcome and evaluated as model output.
9. Risk: historical canon overwritten by later canon. Resolution: versioned genealogy preserves all dated evidence.
10. Risk: one scalar score distorts student development. Resolution: trajectory remains multidimensional.

## Missing implementation work (not architecture defects)
- inventory of the ~250 actual Drive videos;
- chronology/duplicate resolution;
- transcript/subtitle availability audit;
- schema/event implementation for longitudinal extraction;
- pilot extraction on a small chronological sample before bulk processing;
- cost/time estimate based on actual durations and existing transcript coverage.

## Verdict
ARCHITECTURE = PASS
BULK PROCESSING = NOT STARTED
NEXT SAFE STEP = corpus inventory + chronology + transcript coverage, then small pilot before full ~250-video extraction.