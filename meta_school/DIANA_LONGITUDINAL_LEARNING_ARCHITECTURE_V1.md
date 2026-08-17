# DIANA LONGITUDINAL SCHOOL LEARNING ARCHITECTURE v1.0

Status: ARCHITECTURE_DEFINED / CORPUS_NOT_YET_BULK_PROCESSED
Scope: historical corpus ~250 lesson videos, from beginner to current level.

## Mission
Turn the longitudinal Diana corpus into a provenance-grounded model of:
1. School Canon — what the School teaches;
2. Curriculum — sequence/dependencies of learning;
3. Student — Diana's development and retention;
4. Teacher — observed teaching interventions and explanations;
5. Evidence — exact source/time/provenance and confidence;
6. Outcome — retention/generalization/transfer into later independent play.

META coordinates extraction and learning but cannot silently rewrite School canon or methodology.

## Core streams

### CANON
Extract School trading rules, ranges, suit requirements, continuations, interventions, competitive trading, forcing/non-forcing meanings, exceptions, examples and terminology.
Lifecycle: OBSERVED -> REPEATED -> CONSISTENT -> CANON_CANDIDATE -> VERIFIED_CANON.
A single video statement is never automatically canon. Existing written School materials and explicit owner decisions outrank inferred video canon. Conflicts become CANON_CONFLICT with all evidence preserved. Historical versions remain dated; later rules do not overwrite earlier evidence.

### CURRICULUM
Record topic introduction, prerequisites, ordering, revisits, exercise type, depth, spacing and observed transfer checks. Build an evidence-derived prerequisite graph; correlations are hypotheses until validated across sufficient observations/students.

### STUDENT
Track observable skill state through time without treating absence as failure. Distinguish NOT_OBSERVED from FAILED. Store opportunity-to-demonstrate denominator. Suggested observational states (not canonical teaching policy): INTRODUCED, RECOGNIZES_WITH_PROMPT, EXPLAINS, APPLIES_ON_DIRECT_QUESTION, APPLIES_WITH_HINT, INDEPENDENT, RETAINED, GENERALIZED, TRANSFER_CONFIRMED, REGRESSION_AFTER_MASTERY.

### TEACHER
Record observed intervention types: WAIT, QUESTION, DIAGNOSTIC_QUESTION, NEUTRAL_PROMPT, LIGHT_HINT, DIRECTIONAL_HINT, EXPLANATION, DEMONSTRATION, REPLAY, TRANSFER_CHECK. These are descriptive observations, not mandatory methodology. Preserve alternative explanations, analogies, counterexamples and moments of insight.

### EVIDENCE
Every material claim links to video/file identity, lesson date/order, exact time span when available, transcript revision, speaker attribution confidence, extraction version, source trust and transformation lineage. Missing/uncertain evidence remains UNKNOWN. Raw source video is never modified or deleted by META.

### OUTCOME
Separate learning evidence from assessment evidence. Track retention after delay, generalization to changed examples and transfer to unfamiliar/tournament situations. Tournament claims obey tournament evidence restrictions; DDS mathematical claims remain solver-grounded.

## Additional analytical models

### Decision Timeline
For significant decisions record available information, observed choice, stated reasoning, confidence if explicitly observable/elicited, help received, correction and eventual independent outcome.

### Misconception Graph
Group repeated errors only when a shared causal misconception is supported. Store MISCONCEPTION_HYPOTHESIS separately from VERIFIED_MISCONCEPTION. Never infer motive/cause from outcome alone.

### Knowledge Conflict
Represent cases where multiple known rules compete and the wrong applicable rule is selected; distinguish from simple unknown rule.

### Independence / Help Cost
Store actual intervention sequence and count. Any scalar Independence Index is DERIVED/EXPERIMENTAL until separately validated; it is not School canon.

### Forgetting and Recovery
Measure elapsed time since last demonstrated opportunity, later success/failure and amount of help needed to recover. Do not call an unobserved interval forgetting.

### Confidence x Correctness
Use confidence only when explicitly stated or reliably elicited. Never infer psychological confidence solely from tone. When available, distinguish confident-correct, uncertain-correct, confident-incorrect and uncertain-incorrect.

### Decision Latency
Measure only where task start and response boundaries are reliable. Treat latency as contextual evidence, not a direct intelligence/skill score.

### Moment of Insight
Candidate event when learner self-corrects, reformulates a principle or independently explains the mechanism after difficulty. Requires evidence span and is descriptive.

### Latent Learning
Hypothesis when later improvement appears without an immediately preceding direct reteach. Never claim causality without evidence.

### Regression After Mastery
Flag only after prior independent/retained evidence exists; investigate complexity/context before labeling forgetting.

### Second-order habits
Observe planning, information gathering, checking, pausing before action, contradiction detection and willingness to revise. Keep as descriptive patterns unless owner canonizes them.

## Knowledge genealogy
For each CanonItem maintain graph:
source -> first observed teaching -> formulations -> examples -> exceptions -> revisions -> exercises -> student applications -> current verified version.
Every node retains date/version/provenance.

## Canon confidence
Confidence is evidence support, not authority. Inputs may include written canon match, repeated independent lesson occurrences, later confirmation, conflicts and owner decision. Only explicit owner/canonical-source rules can become VERIFIED_CANON without owner review; repeated video evidence alone produces CANON_CANDIDATE.

## Curriculum and content products
Derived, non-canonical products may include:
- FAQ from real student questions;
- typical-error library;
- contrast library (similar situations, different correct decisions);
- minimal-change cases (one parameter changed);
- real teaching-episode library;
- curriculum coverage map;
- canon coverage map: explanation/example/counterexample/exception/exercise/error/transfer/source;
- curriculum bottleneck hypotheses;
- diagnostically useful deal ranking;
- retention/generalization/transfer test sets;
- unresolved learning-debt list.

## Counterfactual and generated exercises
Generated exercises must declare target principle, changed variables and source canon. They are candidates, not historical evidence. Use three levels when useful: familiar retention case -> minimally changed generalization case -> unfamiliar transfer case. DDS validates card-play mathematical properties where applicable; trading correctness comes only from verified School canon.

## Teaching effectiveness research
For each durable skill, attempt to reconstruct:
first explanation -> failed/partial attempts -> alternate explanation/intervention -> exercise -> independent application -> delayed retention -> transfer.
This supports hypotheses about what helped; it does not prove causality from one student and cannot autonomously change methodology.

## Digital Student Model / prediction
A read-only experimental Digital Twin may predict likely response/help need on a future task. Store prediction before the real response; compare prediction vs outcome; use calibration error to improve the model. Never present prediction as a fact about the student. Student/profile persistent writes remain R3.

## Teacher briefing
Before a future lesson, generate a compact evidence-grounded brief: last observed topic, independent/helped attempts, elapsed interval, unresolved retention/transfer checks and suggested diagnostic opportunity. Suggestions are PROPOSAL, not methodology canon.

## Learning debt
Track incomplete loops such as LEARNED_NOT_TRANSFER_TESTED, RETENTION_NOT_RECHECKED, CONFLICT_UNRESOLVED, CANON_GAP, MISCONCEPTION_UNRESOLVED. Debt is evidence state, not criticism of student/teacher.

## Multi-student future
Diana is the first longitudinal reference corpus, not universal truth. When other students exist, compare trajectories without ranking people. Difficulty, bottleneck and teaching-effectiveness claims require cross-student validation before generalization.

## Privacy and reuse
Student-facing raw video/transcript remains protected source evidence. Reuse of clips/examples for other students/public materials requires appropriate permission/de-identification. Derived aggregate patterns should minimize unnecessary personal data.

## Processing pipeline
INVENTORY -> CHRONOLOGY -> SOURCE_QC -> TRANSCRIPT/VIDEO_ANALYSIS -> EPISODE_SEGMENTATION -> SIX_STREAM_EXTRACTION -> PROVENANCE_CHECK -> CANON_CONFLICT_CHECK -> STUDENT_OPPORTUNITY_MODEL -> TEACHER_INTERVENTION_MODEL -> OUTCOME_LINKING -> TEMPORAL_GRAPH -> QC/REVIEW -> LEARNING_MEMORY -> NEXT-LESSON BRIEF.

## Corpus inventory fields
VideoID, DriveID/source, recording date/time, lesson sequence, duration, transcript/subtitle availability, transcript revision, processing status, duplicate/derived-copy relation, participant identity basis, source hash where available, extraction version.
Large videos are not duplicated outside Drive unless necessary; Drive-native copies may be used when free and operationally useful.

## Episode fields
EpisodeID, VideoID, start/end, topic candidates, verified CanonItem links, CurriculumTopic links, StudentSkill links, TeacherIntervention links, decision/opportunity IDs, evidence confidence, outcome links, notes/status.

## Opportunity and assessment fields
OpportunityID, skill/topic, prompted/unprompted, learning_vs_assessment, available information, task novelty, response, correctness basis, help sequence, latency if reliable, confidence if explicit, DDS evidence where applicable, trading-canon evidence where applicable, later retention/generalization/transfer links.

## Anti-bias / anti-overclaim rules
- NOT_OBSERVED != FAILED.
- Helped correct != independent correct.
- One success != mastery.
- One failure != lack of knowledge.
- Repetition != canon authority.
- Temporal sequence != causality.
- Tournament percentage != direct skill measure.
- DDS optimum != what a player could infer from hidden information.
- Model confidence != evidence confidence.
- Student trajectory must not be reduced to one scalar score.

## First longitudinal School Learning Loop
Historical reconstruction: T0 -> ... -> Tn current.
Then prospective loop:
current evidence snapshot -> select unresolved skill/transfer question -> diagnostic task -> pre-registered Student Model prediction -> real response -> evidence/DDS/canon validation -> update Candidate profile projection -> teaching proposal -> future retention/transfer check -> META evaluation of prediction and intervention -> Learning Memory.

## Autonomy
Bulk extraction/observation may operate at A1 read-only with isolated Evidence/Candidate writes. Canon activation, methodology change, student/profile production writes and identity changes are not autonomous. Technical deterministic R1 fixes follow component A2 only where separately granted.
