# DIANA LONGITUDINAL SCHOOL LEARNING ARCHITECTURE v1.1

Status: ARCHITECTURE_HARDENED / CORPUS_NOT_YET_BULK_PROCESSED
Scope: historical corpus ~250 lesson videos, beginner -> current level.

## 1. Mission and authority
Build one provenance-grounded longitudinal knowledge graph from six synchronized views: CANON, CURRICULUM, STUDENT, TEACHER, EVIDENCE, OUTCOME. These are views over shared events/evidence, not six duplicate databases.

Authority order for School trading/methodology:
OWNER_DECISION > VERIFIED_WRITTEN_CANON > VERIFIED_CANON_VERSION > VIDEO_OBSERVATION > MODEL_INFERENCE.
Repeated video evidence increases support but never outranks written/owner canon by repetition alone.
META coordinates extraction and technical learning; it cannot silently activate canon/methodology or write production Student identity/profile state.

## 2. Typed graph: single source of truth
Core immutable/append-only nodes:
- SourceAsset(Video/File/TranscriptRevision);
- LessonSession;
- EvidenceSpan(start/end + speaker/provenance/confidence);
- Episode;
- Claim;
- CanonItem + CanonVersion;
- CurriculumTopic + PrerequisiteHypothesis;
- Skill;
- Opportunity;
- StudentResponse;
- TeacherIntervention;
- OutcomeObservation;
- MisconceptionHypothesis;
- ModelPrediction;
- GeneratedExercise;
- LearningDebt;
- Decision/Review.

Core edges:
EVIDENCES, OCCURS_IN, TEACHES, EXEMPLIFIES, CONTRADICTS, SUPERSEDES, REQUIRES, TARGETS_SKILL, RESPONDS_TO, HELPED_BY, VALIDATED_BY, RETESTS, GENERALIZES, TRANSFERS_TO, DERIVED_FROM, PREDICTS, RESOLVES, GENERATED_FROM.

Rule: derived views never copy authoritative text when a reference/version link is sufficient.

## 3. Evidence classes and claims
Every Claim has `claim_type`, `authority_class`, `evidence_ids`, `valid_from/valid_to` when historical, extraction_version, status and confidence-of-evidence.
Statuses: OBSERVED, HYPOTHESIS, CANDIDATE, VERIFIED, CONFLICTED, SUPERSEDED, REJECTED, UNKNOWN.

Separate confidence dimensions:
- source_quality;
- speaker_attribution_confidence;
- extraction_confidence;
- evidence_support;
- authority_class.
Never collapse them into one confidence number.

## 4. CANON
Extract School trading rules, ranges, suit requirements, continuations, intervention/competitive trading, forcing/non-forcing meanings, exceptions, examples and terminology.
Lifecycle: OBSERVED -> REPEATED/CONSISTENT support -> CANON_CANDIDATE -> VERIFIED_CANON only through existing verified canon or owner decision.
CANON_CONFLICT stores competing formulations, dates, scope and evidence; no automatic winner.
CanonVersion is immutable. Later versions SUPERSEDE rather than overwrite older ones.
Knowledge genealogy: source -> first teaching -> formulations -> examples -> exceptions -> revisions -> exercises -> applications -> current verified version.

## 5. CURRICULUM
Track introduction, prerequisite hypotheses, order, revisits, exercise types, depth, spacing and transfer checks. A prerequisite edge begins HYPOTHESIS and requires repeated longitudinal/cross-student evidence before VERIFIED curriculum knowledge.
Coverage view: topic -> canon -> explanation -> example -> counterexample -> exception -> exercise -> typical error -> retention/generalization/transfer evidence.
Bottlenecks are hypotheses derived from repeated help/reteach/weak-transfer patterns, never automatic methodology changes.

## 6. STUDENT longitudinal model
Opportunity is the denominator. NOT_OBSERVED != FAILED.
Response state is event-level; skill state is a derived temporal projection, never overwritten fact.
Observational states may include INTRODUCED, RECOGNIZES_WITH_PROMPT, EXPLAINS, APPLIES_ON_DIRECT_QUESTION, APPLIES_WITH_HINT, INDEPENDENT, RETAINED, GENERALIZED, TRANSFER_CONFIRMED, REGRESSION_AFTER_MASTERY.
One success != mastery; one failure != lack of knowledge; helped-correct != independent-correct.
Student trajectory remains multidimensional; no single global score.

Decision Timeline stores available information, choice, explicitly stated reasoning, help, correction and later outcome. Cause-of-error remains UNKNOWN unless supported.
Knowledge Conflict distinguishes wrong-rule-selection from unknown-rule.
Misconception Graph stores MISCONCEPTION_HYPOTHESIS separately from VERIFIED_MISCONCEPTION and requires multiple causally compatible observations.

## 7. TEACHER model
TeacherIntervention records observed WAIT, QUESTION, DIAGNOSTIC_QUESTION, NEUTRAL_PROMPT, LIGHT_HINT, DIRECTIONAL_HINT, EXPLANATION, DEMONSTRATION, REPLAY, TRANSFER_CHECK plus free-form observed intervention when taxonomy does not fit.
Taxonomy is descriptive, not mandatory methodology.
Store explanation variants, analogies, counterexamples and MomentOfInsight evidence.
Help cost is the actual intervention sequence/count; any scalar Independence Index remains EXPERIMENTAL.

## 8. OUTCOME model
Learning evidence and assessment evidence are separate.
Outcome levels: immediate response, delayed retention, changed-case generalization, unfamiliar/tournament transfer.
Tournament evidence obeys tournament provenance/identity and missing-trading/play restrictions. DDS mathematical facts are solver-grounded; DDS hidden-information optimum is not automatically human-error proof.
Forgetting requires a later failed/reduced-independence opportunity after prior evidence; an unobserved interval alone is not forgetting.
RegressionAfterMastery requires prior independent/retained evidence and contextual investigation.
LatentLearning is hypothesis only; temporal sequence does not prove causality.

## 9. Measurement safeguards
Confidence x correctness uses confidence only when explicitly stated/reliably elicited; never infer psychological confidence from tone.
Decision latency is recorded only with reliable task-start/response boundaries and remains contextual evidence.
Second-order habits (planning, information gathering, checking, pausing, contradiction detection, revision) remain descriptive unless owner canonizes them.

## 10. Derived libraries/products
Non-canonical derived products:
FAQ from real questions; typical-error library; contrast library; minimal-change cases; teaching-episode library; curriculum/canon coverage maps; bottleneck hypotheses; diagnostic-deal ranking; retention/generalization/transfer sets; learning-debt queue.
GeneratedExercise always declares target principle, verified canon version, changed variables, source evidence and generation version. It never becomes historical evidence.
Where useful: retention familiar case -> minimally changed generalization -> unfamiliar transfer.
DDS validates card-play mathematical properties where applicable; trading correctness uses verified School canon only.

## 11. Teaching-effectiveness research
Reconstruct durable-skill chains: first explanation -> partial/failure -> alternative intervention -> exercise -> independent application -> delayed retention -> transfer.
Output is EFFECTIVENESS_HYPOTHESIS for Diana. General methodology claims require cross-student evidence and owner decision.
Preserve failed teaching episodes; never filter the corpus to successes only.

## 12. Digital Student Model
Read-only experimental Digital Twin predicts response/help need. Prediction must be persisted before outcome and scored afterward. Prediction is model output, not student fact.
Evaluate calibration by skill/topic/novelty/help class, not one global accuracy number.
Production Student/profile/identity writes remain R3.

## 13. Teacher briefing and Learning Debt
Teacher briefing: evidence-grounded last observation, helped/independent history, elapsed interval, unresolved retention/transfer/conflict and suggested diagnostic opportunity. All suggestions = PROPOSAL.
LearningDebt types include LEARNED_NOT_TRANSFER_TESTED, RETENTION_NOT_RECHECKED, CONFLICT_UNRESOLVED, CANON_GAP, MISCONCEPTION_UNRESOLVED. Debt is workflow state, not criticism.

## 14. Privacy and reuse
Raw video/transcript is protected source evidence. Original sources are immutable. Public/other-student reuse of identifiable clips requires permission/de-identification. Derived aggregate patterns minimize personal data. Large videos are not duplicated outside Drive unless necessary; free Drive-native copies may be used when operationally useful.

## 15. Optimized processing pipeline
### Phase A — cheap corpus pass
INVENTORY -> CHRONOLOGY -> DUPLICATE/DERIVED_RELATION -> TRANSCRIPT/SUBTITLE COVERAGE -> SOURCE_QC.
No full video download when metadata/transcript is sufficient.

### Phase B — text-first extraction
For videos with reliable transcript/subtitles: segment transcript -> detect candidate episodes -> extract claims/topics/opportunities/interventions -> provenance/QC.
Do not invoke expensive visual/audio analysis for segments with sufficient evidence.

### Phase C — selective multimodal escalation
Escalate only when required for: cards/board position not represented in transcript, ambiguous speaker, gesture/visual teaching evidence, missing/low-quality transcript, or high-value conflict/assessment episode.
Store escalation reason.

### Phase D — linking
CANON_CONFLICT_CHECK -> TEMPORAL_LINKING -> OPPORTUNITY/RESPONSE -> TEACHER_INTERVENTION -> OUTCOME_LINKING -> MISCONCEPTION/PREREQUISITE hypotheses -> LEARNING_DEBT.

### Phase E — QC
Deterministic provenance/completeness checks first; model critic second; owner review only for R4/canon conflict/meaningful ambiguity.

### Phase F — outputs
Temporal graph -> current Candidate Student projection -> Canon/Curriculum candidates -> Teacher brief -> Learning Memory.

This staged pipeline avoids repeatedly reprocessing the full 250-video corpus when only a small subset requires multimodal review.

## 16. Incremental processing and idempotency
Each extraction is keyed by `(SourceAsset revision/hash, extraction_version, segment locator)`.
Unchanged assets are not reprocessed after algorithm updates unless the affected extraction module/version changes or a dependency invalidates derived results.
New video processing appends T(n+1) and recomputes only impacted projections/edges.
Corrections create new Evidence/Claim versions; no destructive rewrite.

## 17. Corpus inventory
VideoID, DriveID/source, recording date/time, lesson sequence, duration, transcript/subtitle availability and quality, transcript revision, processing status, duplicate/derived relation, participant identity basis, source hash where available, extraction versions, escalation status/reason.
Chronology confidence is explicit when date/order is uncertain.

## 18. Episode / Opportunity schema
Episode: EpisodeID, VideoID, start/end, topic candidates, CanonVersion links, CurriculumTopic links, Skill links, intervention links, decision/opportunity IDs, evidence IDs, extraction status.
Opportunity: OpportunityID, skill/topic, prompted/unprompted, learning_vs_assessment, available information, novelty, response, correctness basis, help sequence, reliable latency, explicit confidence, DDS evidence where applicable, verified-trading-canon evidence, later retention/generalization/transfer links.

## 19. Cross-system links
Video analysis supplies transcript/episode Evidence; it does not decide canon.
DDS supplies mathematical card-play Evidence; it does not decide School trading/methodology.
Tournament analysis supplies independent transfer/outcome Evidence under its source limits.
Lesson/material generator consumes VERIFIED_CANON + Curriculum/Student proposals and must preserve source links.
Student Model consumes published/validated observations; it cannot activate canon.
META evaluates system health/improvements and preserves authority boundaries.

## 20. Multi-student future
Diana is first longitudinal reference corpus, not universal truth. Cross-student layer uses anonymous/controlled aggregates where possible. Difficulty, prerequisite, bottleneck and teaching-effectiveness generalization require multiple students and explicit validation; do not rank people.

## 21. First longitudinal Learning Loop
Historical: T0 -> ... -> Tn reconstruction.
Prospective: current evidence snapshot -> unresolved skill/transfer question -> diagnostic task -> pre-registered prediction -> real response -> DDS/canon/evidence validation -> Candidate profile projection -> teaching proposal -> delayed retention/transfer check -> META evaluation -> Learning Memory.

## 22. Autonomy
A1 read-only bulk observation/extraction may write isolated Evidence/Candidate artifacts. Canon activation, methodology change, production Student/profile/identity writes remain non-autonomous. Technical deterministic R1 fixes use A2 only where that component separately earned it.
