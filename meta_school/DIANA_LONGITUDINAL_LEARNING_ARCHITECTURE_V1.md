# DIANA LONGITUDINAL SCHOOL LEARNING ARCHITECTURE v1.3

Status: ARCHITECTURE_HARDENED / KNOWLEDGE_ACQUISITION_INTEGRATED / RETRIEVAL_AND_ACTIVE_REVIEW_INTEGRATED / CORPUS_NOT_YET_BULK_PROCESSED
Scope: historical corpus ~250 lesson videos, beginner -> current level.

## 1. Mission and authority
One provenance-grounded graph with views CANON, CURRICULUM, STUDENT, TEACHER, EVIDENCE, OUTCOME, KNOWLEDGE. Authority for School trading/methodology: OWNER_DECISION > VERIFIED_WRITTEN_CANON > VERIFIED_CANON_VERSION > VIDEO_OBSERVATION > MODEL_INFERENCE. Repetition/support never substitutes for authority.

## 2. Typed graph
Append-only/versioned nodes: SourceAsset, TranscriptRevision, LessonSession, EvidenceSpan, Episode, Claim, KnowledgeItem, KnowledgeVersion, CanonItem, CanonVersion, CurriculumTopic, PrerequisiteHypothesis, Skill, Opportunity, StudentResponse, TeacherIntervention, OutcomeObservation, MisconceptionHypothesis, ModelPrediction, GeneratedExercise, LearningDebt, KnowledgeGap, TerminologyConcept, TerminologyAlias, RetrievalEvent, ReviewQueueItem, Decision/Review.
Edges include EVIDENCES, SUPPORTS, TEACHES, EXEMPLIFIES, CONTRADICTS, SUPERSEDES, REQUIRES, TARGETS_SKILL, RESPONDS_TO, HELPED_BY, VALIDATED_BY, RETESTS, GENERALIZES, TRANSFERS_TO, DERIVED_FROM, PREDICTS, RESOLVES, GENERATED_FROM, DUPLICATE_OF, CLOSES_GAP, HAS_VERSION, ALIAS_OF, USED_BY.
Derived views reference immutable versions instead of copying authoritative content.

## 3. Evidence/claims
Claim stores type, authority, evidence, historical validity, extraction version, status and separate source/speaker/extraction/support/authority dimensions. Statuses OBSERVED, HYPOTHESIS, CANDIDATE, VERIFIED, CONFLICTED, SUPERSEDED, REJECTED, UNKNOWN. No single confidence score.

## 4. Knowledge Acquisition & Consolidation
Sources: Video, Tournament, DDS, Lesson/Material, verified School docs, approved bridge literature/other sources.
SOURCE -> EvidenceSpan -> Claim -> KNOWLEDGE_CANDIDATE -> entity/semantic match -> dedupe -> scope normalization -> contradiction -> authority -> KnowledgeVersion candidate -> QC -> VERIFIED_KNOWLEDGE / CANON_CANDIDATE / CONFLICT -> graph -> Gap detection -> use -> Outcome -> META.

Authority classes: VERIFIED_CANON, VERIFIED_KNOWLEDGE, CANDIDATE_KNOWLEDGE, HISTORICAL, CONFLICTED, REJECTED.
Rules: many observations link to one item; near-duplicate SAME_AS remains candidate until validated; scope is first-class; newer != more authoritative; corrections append versions; downstream consumers pin exact versions; each version records validation basis, known limits/exceptions and dependencies.

## 5. Knowledge Gap / retrieval-miss loop
Gap types: MISSING_RULE, MISSING_EXPLANATION, MISSING_EXAMPLE, MISSING_COUNTEREXAMPLE, MISSING_EXCEPTION, MISSING_EXERCISE, MISSING_TRANSFER_CHECK, CONFLICT_UNRESOLVED, SOURCE_WEAK, COVERAGE_WEAK, RETRIEVAL_MISS.
Failed retrieval during lesson/material/analysis work logs RetrievalEvent with query/context, authority/scope filters, candidate hits and result. A genuine miss may create/strengthen KnowledgeGap; repeated semantically equivalent misses consolidate rather than duplicate gaps. Later evidence/version CLOSES_GAP while history remains.

## 6. Retrieval architecture
Retrieval is two-stage:
1. candidate retrieval by exact IDs/aliases/lexical and optional semantic embedding index;
2. authority/scope/version/provenance filter + ranking.
Embeddings are an index only, never evidence or authority. A semantically similar candidate cannot override verified canon.
Student-facing canonical retrieval defaults to current VERIFIED_CANON within matching scope. Research/audit may explicitly include historical/candidate/conflicted items with labels.
Every consequential generated material records exact retrieved KnowledgeVersion/CanonVersion IDs.

## 7. Multilingual terminology layer
TerminologyConcept has stable concept identity; TerminologyAlias stores language, display form, status, valid dates/scope and source. Alias does not create a new knowledge item.
School-facing Russian preferred term: «торговля», not «аукцион». Legacy/source wording remains preserved in evidence and may be searchable as historical alias without being used in current School-facing output.
Future Hebrew/English aliases may map to the same concept without changing canon meaning.

## 8. Active Review prioritization
ReviewQueue prioritizes human/expensive validation by impact, not novelty alone. Signals may include: unresolved canon conflict, high downstream usage, high-frequency retrieval miss, high-use KnowledgeGap, weak provenance on widely used knowledge, repeated candidate disagreement, student-safety/identity boundary, or dependency staleness.
Priority score is operational/experimental and cannot grant authority. R4/canon decisions still require owner authority.

## 9. Impact-aware correction
Before superseding/correcting KnowledgeVersion, enumerate USED_BY downstream references: lessons, exercises, curriculum nodes, generated artifacts, student assessments/projections and other knowledge. Determine which outputs require revalidation/regeneration. Never silently rewrite historical artifacts; create new versions and mark affected derived artifacts NEEDS_REVALIDATION where appropriate.

## 10. Knowledge compression / summaries
Concise cards, cheat-sheets and summaries are GeneratedKnowledgeViews referencing exact source versions. They are regenerable projections, not new authority. Compression must preserve exceptions/scope or explicitly link to full version. A summary cannot become sole provenance.

## 11. CANON
Trading rules, ranges, suit requirements, continuations, intervention/competitive trading, forcing/non-forcing meanings, exceptions/examples/terminology. OBSERVED -> support -> CANON_CANDIDATE -> VERIFIED_CANON only through verified canon/owner. Conflicts preserve competing scope/evidence. Versions supersede, never overwrite. Genealogy links source -> teaching -> formulations -> examples -> exceptions -> revisions -> exercises -> applications -> current version.

## 12. CURRICULUM
Track introduction, prerequisite hypotheses, order, revisits, exercises, spacing, transfer. Prerequisites require validation. Coverage links topic -> canon/knowledge -> explanation/example/counterexample/exception/exercise/error -> retention/generalization/transfer. Bottlenecks remain hypotheses.

## 13. STUDENT
Opportunity denominator; NOT_OBSERVED != FAILED. Event responses feed derived temporal skill projection. Suggested descriptive states: INTRODUCED, RECOGNIZES_WITH_PROMPT, EXPLAINS, APPLIES_ON_DIRECT_QUESTION, APPLIES_WITH_HINT, INDEPENDENT, RETAINED, GENERALIZED, TRANSFER_CONFIRMED, REGRESSION_AFTER_MASTERY. Helped correct != independent correct; no global scalar score. Decision timeline, KnowledgeConflict and Misconception hypotheses remain evidence-linked.

## 14. TEACHER
Observed intervention taxonomy plus free-form fallback; descriptive only. Preserve alternative/failed explanations, analogies, counterexamples, insight episodes. Actual help sequence retained; scalar independence remains experimental.

## 15. OUTCOME / measurement
Separate learning vs assessment and immediate/retention/generalization/transfer. Tournament restrictions and DDS solver authority retained. Hidden-information DDS optimum != automatic human error. Forgetting/regression need later opportunity evidence. Confidence only explicit/reliably elicited; latency only reliable; temporal order != causality.

## 16. Derived products and generated exercises
FAQ, error/contrast/minimal-change libraries, episode library, coverage maps, bottleneck hypotheses, diagnostic deals, test sets, debt/gap queues. GeneratedExercise pins target principle, exact Canon/Knowledge versions, changed variables, evidence and generator version; generated content is not historical evidence. Retention -> generalization -> transfer progression where useful.

## 17. Teaching-effectiveness / Digital Student Model
Effectiveness chains produce hypotheses for Diana; general methodology needs cross-student validation + owner. Preserve failures. Digital Student Model remains read-only, pre-registers predictions before outcome and calibrates by skill/topic/novelty/help. Production profile/identity writes R3.

## 18. Teacher briefing / debt
Briefing: last evidence, independent/helped history, elapsed interval, unresolved retention/transfer/conflict/gaps and diagnostic proposal. LearningDebt workflow state includes transfer/retention/conflict/canon/misconception gaps; not criticism.

## 19. Privacy/reuse
Raw sources protected/immutable. Identifiable reuse needs permission/de-identification. Aggregates minimize personal data. Large video duplication outside Drive avoided unless necessary; free Drive-native copies allowed when useful.

## 20. Optimized processing
A INVENTORY/CHRONOLOGY/DUPLICATES/TRANSCRIPT COVERAGE/SOURCE QC.
B transcript-first segmentation/extraction/provenance.
C selective multimodal escalation only for missing visual/card/speaker/high-value evidence.
D consolidation: entity match/dedupe/scope/conflict/Canon+Knowledge candidates/temporal+student+teacher+outcome links/gaps/debt/retrieval index.
E QC deterministic first, critic second, owner R4/conflict.
F outputs graph/projections/brief/Learning Memory/retrieval views/review queue.

## 21. Incrementality
Extraction key = source revision/hash + module/version + segment. Consolidation key adds normalized identity+scope. Unchanged source not reprocessed unless affected dependency/module changes. New T(n+1) recomputes impacted neighborhoods only. Corrections append versions. Semantic index can be rebuilt independently from authority graph.

## 22. Inventory / episode / opportunity
Inventory includes source/date/order/duration/transcript quality/revision/status/duplicates/identity/hash/extraction/escalation/chronology confidence. Episode links source/time/topics/versions/curriculum/skill/interventions/opportunities/evidence. Opportunity stores prompt mode, learning/assessment, available info, novelty, response/correctness basis, help, reliable latency, explicit confidence, DDS/canon evidence and later outcomes.

## 23. Cross-system responsibilities
Video -> Evidence/episodes, not authority. DDS -> mathematical play evidence, not trading/methodology. Tournament -> transfer/outcome within source limits. Literature -> external candidates, never School-canon override. Lesson generator -> exact verified versions + proposals and provenance. Student Model -> validated observations, no authority activation. META -> health/dedupe/conflict/gap/review queues and technical improvements without authority inheritance.

## 24. Knowledge health
Track duplicate candidate rate, conflicts/count+age, provenance completeness, candidate->verified conversion, rejected-repeat rate, gap coverage, stale dependencies, orphan versions, downstream usage, corrections, retrieval misses, review queue age, regeneration/revalidation impact and cost/time per verified addition. No arbitrary thresholds without evidence/owner policy.

## 25. Multi-student future
Diana first reference, not universal truth. Cross-student anonymized/controlled aggregates may estimate misconception frequency, difficulty, prerequisites, bottlenecks and effectiveness only after sufficient validation; do not rank people.

## 26. Learning Loop
Historical T0...Tn plus prospective: evidence snapshot -> unresolved skill/knowledge/transfer -> diagnostic task -> pre-registered prediction -> response -> validation -> Candidate profile/knowledge -> teaching proposal -> delayed check -> META evaluation -> Learning Memory/Knowledge consolidation -> retrieval/Gap feedback.

## 27. Autonomy
A1 read-only extraction may write isolated Evidence/Candidate/Gap/Retrieval/Review artifacts. Semantic indexing is non-authoritative. VERIFIED_KNOWLEDGE automation only where deterministic provenance/authority policy explicitly permits. Canon/methodology activation and production Student/profile/identity writes remain non-autonomous; technical R1 A2 only per component authority.