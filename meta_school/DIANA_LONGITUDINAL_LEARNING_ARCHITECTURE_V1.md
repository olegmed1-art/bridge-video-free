# DIANA LONGITUDINAL SCHOOL LEARNING ARCHITECTURE v1.2

Status: ARCHITECTURE_HARDENED / KNOWLEDGE_ACQUISITION_INTEGRATED / CORPUS_NOT_YET_BULK_PROCESSED
Scope: historical corpus ~250 lesson videos, beginner -> current level.

## 1. Mission and authority
Build one provenance-grounded longitudinal knowledge graph from synchronized views: CANON, CURRICULUM, STUDENT, TEACHER, EVIDENCE, OUTCOME and KNOWLEDGE. Views share typed source events; they are not duplicate databases.
Authority for School trading/methodology: OWNER_DECISION > VERIFIED_WRITTEN_CANON > VERIFIED_CANON_VERSION > VIDEO_OBSERVATION > MODEL_INFERENCE. Repetition increases evidence support, not authority.

## 2. Typed graph — single source of truth
Core immutable/append-only nodes: SourceAsset, TranscriptRevision, LessonSession, EvidenceSpan, Episode, Claim, KnowledgeItem, KnowledgeVersion, CanonItem, CanonVersion, CurriculumTopic, PrerequisiteHypothesis, Skill, Opportunity, StudentResponse, TeacherIntervention, OutcomeObservation, MisconceptionHypothesis, ModelPrediction, GeneratedExercise, LearningDebt, KnowledgeGap, Decision/Review.
Core edges: EVIDENCES, OCCURS_IN, SUPPORTS, TEACHES, EXEMPLIFIES, CONTRADICTS, SUPERSEDES, REQUIRES, TARGETS_SKILL, RESPONDS_TO, HELPED_BY, VALIDATED_BY, RETESTS, GENERALIZES, TRANSFERS_TO, DERIVED_FROM, PREDICTS, RESOLVES, GENERATED_FROM, DUPLICATE_OF, CLOSES_GAP, HAS_VERSION.
Derived views reference immutable versions instead of copying authoritative content.

## 3. Evidence and claim model
Every Claim has type, authority class, evidence IDs, historical validity when applicable, extraction version, status and separated confidence dimensions: source quality, speaker attribution, extraction confidence, evidence support, authority. Statuses: OBSERVED, HYPOTHESIS, CANDIDATE, VERIFIED, CONFLICTED, SUPERSEDED, REJECTED, UNKNOWN. Never collapse confidence and authority.

## 4. Knowledge Acquisition & Consolidation Loop
Sources may include Video, Tournament, DDS, Lesson/Material, verified School document, bridge literature and other explicitly approved evidence sources.
Pipeline:
SOURCE -> EvidenceSpan -> Claim -> KNOWLEDGE_CANDIDATE -> entity/semantic matching -> duplicate/near-duplicate detection -> contradiction check -> authority classification -> KnowledgeVersion candidate -> validation/QC -> VERIFIED_KNOWLEDGE or CANON_CANDIDATE/CONFLICT -> graph links -> Gap detection -> downstream use -> Outcome -> META feedback.

### Knowledge authority classes
- VERIFIED_CANON: owner/verified canonical School trading or methodology version.
- VERIFIED_KNOWLEDGE: validated factual/educational knowledge not itself canon.
- CANDIDATE_KNOWLEDGE: useful extraction awaiting sufficient validation.
- HISTORICAL: prior/superseded knowledge retained for provenance/history.
- CONFLICTED: unresolved incompatible claims.
- REJECTED: candidate disproved/not accepted; retained to avoid repeated work.

### Consolidation rules
1. Forty observations of one rule produce one KnowledgeItem/CanonItem with many Evidence links, not forty rules.
2. Exact duplicates are merged by identity/reference; near-duplicates become candidate SAME_AS links until semantic equivalence is validated.
3. Scope is first-class: level, context, vulnerability, seat/position, competition context, date/version and prerequisites where relevant. Apparent contradictions with different scopes are not conflicts.
4. Newer is not automatically better/correct. Historical sequence does not grant authority.
5. Corrections create new versions; originals remain immutable.
6. Knowledge used downstream must point to the exact KnowledgeVersion/CanonVersion used.
7. A KnowledgeVersion records validation basis and known exceptions/limits.

### Knowledge Gap loop
Gap types include MISSING_RULE, MISSING_EXPLANATION, MISSING_EXAMPLE, MISSING_COUNTEREXAMPLE, MISSING_EXCEPTION, MISSING_EXERCISE, MISSING_TRANSFER_CHECK, CONFLICT_UNRESOLVED, SOURCE_WEAK, COVERAGE_WEAK.
A later Evidence/KnowledgeVersion may CLOSES_GAP; gap history is retained. Gap detection creates work candidates, not automatic invented content.

### Provenance completeness gate
No VERIFIED_KNOWLEDGE without resolvable provenance and validation basis. If source locator/revision is missing, status remains CANDIDATE/UNKNOWN. Generated summaries never become the sole provenance of the knowledge they summarize.

### Retrieval/use policy
Retrieval ranks authority and scope fit before repetition/popularity. Current VERIFIED_CANON is used for School trading. Historical/candidate/conflicted items may be shown for research/audit but cannot silently drive student-facing canonical instruction.

### Knowledge decay/staleness
Knowledge is not deleted merely because it is old. Mark STALE only when a dependency/version/source changes or explicit review policy requires revalidation. CanonVersion validity is versioned; historical remains queryable.

## 5. CANON
Extract School trading rules, ranges, suit requirements, continuations, intervention/competitive trading, forcing/non-forcing meanings, exceptions, examples and terminology. Lifecycle: OBSERVED -> support -> CANON_CANDIDATE -> VERIFIED_CANON only through verified canon/owner authority. CANON_CONFLICT stores competing formulations/scopes/evidence; no automatic winner. Later versions SUPERSEDE rather than overwrite.
Knowledge genealogy: source -> first teaching -> formulations -> examples -> exceptions -> revisions -> exercises -> applications -> current verified version.

## 6. CURRICULUM
Track introductions, prerequisite hypotheses, order, revisits, exercise types, spacing and transfer checks. Prerequisite begins HYPOTHESIS and needs repeated/cross-student validation. Coverage view links topic -> canon/knowledge -> explanation -> example -> counterexample -> exception -> exercise -> error -> retention/generalization/transfer. Bottlenecks remain hypotheses until validated.

## 7. STUDENT
Opportunity is denominator; NOT_OBSERVED != FAILED. Response is event-level; skill state is derived temporal projection. Observational states may include INTRODUCED, RECOGNIZES_WITH_PROMPT, EXPLAINS, APPLIES_ON_DIRECT_QUESTION, APPLIES_WITH_HINT, INDEPENDENT, RETAINED, GENERALIZED, TRANSFER_CONFIRMED, REGRESSION_AFTER_MASTERY. One success != mastery; one failure != lack of knowledge; helped correct != independent correct. No global scalar student score.
Decision Timeline stores available information, choice, stated reasoning, help, correction and later outcome. Cause remains UNKNOWN unless supported. Knowledge Conflict and Misconception hypotheses are separate typed objects.

## 8. TEACHER
Record observed WAIT, QUESTION, DIAGNOSTIC_QUESTION, NEUTRAL_PROMPT, LIGHT_HINT, DIRECTIONAL_HINT, EXPLANATION, DEMONSTRATION, REPLAY, TRANSFER_CHECK plus free-form fallback. Taxonomy is descriptive, not mandatory methodology. Preserve explanation variants, analogies, counterexamples, failed explanations and moments of insight. Help cost is actual intervention sequence; scalar Independence Index remains experimental.

## 9. OUTCOME
Separate learning from assessment. Link immediate response, delayed retention, changed-case generalization and unfamiliar/tournament transfer. Tournament evidence obeys source/identity/missing-trading/play restrictions. DDS facts are solver-grounded; hidden-information optimum is not automatic human-error proof. Forgetting/regression require later opportunity evidence; latent learning/effectiveness remain causal hypotheses.

## 10. Measurement safeguards
Confidence x correctness only with explicit/reliably elicited confidence; no tone-based psychology. Latency only with reliable boundaries. Second-order habits remain descriptive unless owner canonizes them. Temporal order != causality; tournament percentage != direct skill measure; model confidence != evidence confidence.

## 11. Derived products
FAQ, typical-error library, contrast/minimal-change cases, teaching-episode library, curriculum/canon/knowledge coverage maps, bottleneck hypotheses, diagnostic-deal ranking, retention/generalization/transfer sets, learning-debt and knowledge-gap queues. Derived products remain versioned and non-canonical unless explicitly promoted.
GeneratedExercise declares target principle, exact verified Canon/Knowledge versions, changed variables, source evidence and generation version. It never becomes historical evidence by being generated.

## 12. Teaching-effectiveness research
Reconstruct durable-skill chains from explanation through transfer. Outputs are EFFECTIVENESS_HYPOTHESIS for Diana. Cross-student validation + owner decision required for general methodology. Preserve negative/failed episodes.

## 13. Digital Student Model
Read-only experimental prediction, persisted before real outcome and scored afterward. Calibrate by skill/topic/novelty/help, not global accuracy. Prediction is not student fact. Production profile/identity writes remain R3.

## 14. Teacher briefing / learning debt
Briefing shows last evidence, independent/helped attempts, elapsed interval, unresolved retention/transfer/conflict/gaps and suggested diagnostic opportunity; suggestions = PROPOSAL. LearningDebt includes LEARNED_NOT_TRANSFER_TESTED, RETENTION_NOT_RECHECKED, CONFLICT_UNRESOLVED, CANON_GAP, MISCONCEPTION_UNRESOLVED.

## 15. Privacy/reuse
Raw source video/transcript protected and immutable. Reuse identifiable clips requires permission/de-identification. Aggregates minimize personal data. Large videos are not duplicated outside Drive unless necessary; free Drive-native copies may be used when operationally useful.

## 16. Optimized staged processing
A cheap pass: INVENTORY -> CHRONOLOGY -> duplicate/derived relation -> transcript/subtitle coverage -> Source QC.
B text-first: transcript segmentation -> candidate episodes/claims/topics/opportunities/interventions -> provenance/QC.
C selective multimodal: only missing cards/visual context, speaker ambiguity, poor transcript, high-value conflict/assessment; store reason.
D consolidation/linking: entity matching -> knowledge dedupe -> scope normalization -> conflict detection -> Canon/Knowledge candidates -> temporal/student/teacher/outcome links -> Gap/debt detection.
E QC: deterministic provenance/completeness first, independent/model critic second, owner review for R4/canon conflict/meaningful ambiguity.
F outputs: graph/projections -> Teacher brief -> Learning Memory -> downstream retrieval.

## 17. Incrementality/idempotency
Extraction key = SourceAsset revision/hash + extraction module/version + segment locator. Consolidation key additionally includes normalized candidate identity + scope. Unchanged assets are not reprocessed unless affected module/dependency changes. New video appends T(n+1) and recomputes impacted graph neighborhoods only. Corrections append versions.

## 18. Corpus inventory / episode / opportunity
Inventory: VideoID, Drive source, recording date/time, lesson order, duration, transcript/subtitle coverage/quality/revision, processing status, duplicate relation, identity basis, source hash, extraction versions, escalation reason, chronology confidence.
Episode: source/time, topic candidates, Canon/KnowledgeVersion links, Curriculum/Skill links, intervention/opportunity IDs, Evidence IDs/status.
Opportunity: skill/topic, prompted/unprompted, learning/assessment, available information, novelty, response/correctness basis, help, reliable latency, explicit confidence, DDS/canon evidence, later outcome links.

## 19. Cross-system responsibilities
Video -> Evidence/episodes, never canon authority.
DDS -> mathematical card-play evidence, never trading/methodology authority.
Tournament -> independent transfer/outcome evidence within source limits.
Books/literature -> external knowledge candidates with source/provenance; they do not override School canon.
Lesson generator -> consumes exact VERIFIED_CANON/VERIFIED_KNOWLEDGE versions + proposals and preserves source links.
Student Model -> consumes validated/published observations; cannot activate canon/knowledge authority.
META -> evaluates health, dedupe/conflict/gap queues and technical improvements without authority inheritance.

## 20. Knowledge-base health metrics
Track without inventing arbitrary pass thresholds: duplicate candidate rate, unresolved conflict count/age, provenance completeness, candidate-to-verified conversion, rejected/repeated hypothesis rate, gap coverage, stale dependencies, orphan KnowledgeVersions, downstream usage by version, corrections, retrieval misses and cost/time per verified knowledge addition. Thresholds require evidence/owner policy.

## 21. Multi-student future
Diana is first reference corpus, not universal truth. Difficulty/prerequisite/bottleneck/effectiveness generalization requires multiple students and explicit validation; use controlled aggregates and do not rank people.

## 22. First longitudinal Learning Loop
Historical T0...Tn reconstruction plus prospective: evidence snapshot -> unresolved skill/knowledge/transfer question -> diagnostic task -> pre-registered prediction -> response -> DDS/canon/evidence validation -> Candidate profile/knowledge update -> teaching proposal -> delayed check -> META evaluation -> Learning Memory/Knowledge consolidation.

## 23. Autonomy
A1 read-only bulk extraction may write isolated Evidence/Candidate/Gap artifacts. VERIFIED_KNOWLEDGE promotion may be automated only where deterministic authority/provenance rules explicitly permit it; School Canon activation, methodology change, production Student/profile/identity writes remain non-autonomous. Technical R1 fixes use A2 only per separately earned component authority.
