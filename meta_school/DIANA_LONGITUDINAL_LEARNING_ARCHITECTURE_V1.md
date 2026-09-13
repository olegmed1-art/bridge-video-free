# DIANA LONGITUDINAL SCHOOL LEARNING ARCHITECTURE v1.4

Status: ARCHITECTURE_HARDENED / KNOWLEDGE+RETRIEVAL+CURRICULUM_BUILDER_INTEGRATED / CORPUS_NOT_YET_BULK_PROCESSED
Scope: ~250 Diana lesson videos, beginner -> current level.

## Mission
One provenance-grounded graph supporting CANON, KNOWLEDGE, CURRICULUM, STUDENT, TEACHER, EVIDENCE and OUTCOME, plus a School Curriculum Builder that reconstructs actual learning history and proposes a multi-year School course from zero to advanced.
Authority for School trading/methodology: OWNER_DECISION > VERIFIED_WRITTEN_CANON > VERIFIED_CANON_VERSION > VIDEO_OBSERVATION > MODEL_INFERENCE. Repetition never substitutes for authority.

## Typed graph
Versioned/append-only nodes include SourceAsset, TranscriptRevision, LessonSession, EvidenceSpan, Episode, Claim, KnowledgeItem/Version, CanonItem/Version, CurriculumProgram/Version, CurriculumStage, CurriculumDomain, CurriculumModule, CurriculumTopic, LearningObjective, PrerequisiteHypothesis, Skill, Opportunity, StudentResponse, TeacherIntervention, OutcomeObservation, MisconceptionHypothesis, ModelPrediction, GeneratedExercise, LearningDebt, KnowledgeGap, CurriculumGap, TerminologyConcept/Alias, RetrievalEvent, ReviewQueueItem, Decision/Review.
Important edges include EVIDENCES, SUPPORTS, TEACHES, EXEMPLIFIES, CONTRADICTS, SUPERSEDES, REQUIRES, TARGETS_SKILL, RESPONDS_TO, HELPED_BY, VALIDATED_BY, RETESTS, GENERALIZES, TRANSFERS_TO, DERIVED_FROM, PREDICTS, RESOLVES, GENERATED_FROM, DUPLICATE_OF, CLOSES_GAP, HAS_VERSION, ALIAS_OF, USED_BY, BELONGS_TO_STAGE, BELONGS_TO_DOMAIN, HISTORICALLY_PRECEDES, PROPOSED_BEFORE.

## Curriculum Builder
Maintain three distinct projections:
1 HISTORICAL_CURRICULUM — immutable reconstruction of what Diana actually studied, by date and historical learning year/stage.
2 STRUCTURED_CURRICULUM_GRAPH — normalized topics/skills/canon/knowledge, vertical spirals and prerequisite hypotheses.
3 CANDIDATE_SCHOOL_CURRICULUM — proposed general School sequence from beginner to advanced; never normative until owner activation.

Every topic may have both DianaHistoricalStage and ProposedSchoolStage. Moving a topic in Candidate Curriculum never rewrites history.
Course hierarchy: Program -> Year/Stage -> Domain -> Module -> Topic -> Subtopic/LearningObjective -> Exercise/Opportunity -> Assessment/Transfer.
Years/stages are not pre-imposed: infer natural boundaries from chronology, prerequisites, complexity, revisits, independence/retention/transfer evidence, then propose them.
Initial domain labels (основы/счёт, торговля, конкурентная торговля/интервенция, розыгрыш, защита, первый ход, техника, планирование, турнирная практика) are candidates and must be confirmed/expanded by School evidence.
Recurring topics form vertical spirals rather than duplicates.

Each Module Card links prerequisites, objectives, exact verified Canon/Knowledge versions, terminology, explanations, examples/counterexamples/exceptions, errors/misconceptions, deals, exercises/homework, understanding checks, independent assessment, delayed retention, generalization, transfer/tournament evidence, sources/timestamps, gaps/conflicts, difficulty evidence and downstream modules. Missing pieces create explicit gaps, not invented content.

Historical repetition is classified before influencing general course order: planned spiral, remediation, forgetting/recovery, misconception, enrichment or unknown. Diana-specific remediation is not automatically copied into the School course.
Each Candidate Stage may have multidimensional EntryProfile/ExitProfile based on observable capabilities; no invented score/threshold.

## Knowledge Acquisition / retrieval
SOURCE -> Evidence -> Claim -> KnowledgeCandidate -> entity/semantic match -> dedupe -> scope normalization -> contradiction -> authority -> KnowledgeVersion -> QC -> verified/candidate/conflict -> Gap -> use/outcome -> META.
Authority classes: VERIFIED_CANON, VERIFIED_KNOWLEDGE, CANDIDATE_KNOWLEDGE, HISTORICAL, CONFLICTED, REJECTED. Many observations link to one item. Scope is first-class; newer != authoritative; corrections append versions; downstream pins exact versions.
Retrieval is candidate search (IDs/aliases/lexical/optional embeddings) followed by authority/scope/version/provenance filtering. Embeddings are index only. Retrieval misses can create consolidated KnowledgeGap/CurriculumGap.

## Terminology
Stable concept + multilingual aliases. Current School-facing Russian uses «торговля», not «аукцион»; legacy/source wording remains searchable evidence. Future Hebrew/English aliases do not change meaning/canon.

## Active review / impact
Review high-impact canon conflicts, widely used weak-provenance knowledge, frequent retrieval misses/gaps, dependency staleness and R3/R4 boundaries first. Priority never grants authority. Before correcting a used version, enumerate USED_BY dependencies and mark affected derived artifacts/modules NEEDS_REVALIDATION rather than silently rewriting history.

## Student/Teacher/Outcome safeguards
Opportunity denominator; NOT_OBSERVED != FAILED; helped correct != independent correct; one success != mastery; no global student score. Decision timeline, KnowledgeConflict and Misconception remain evidence-linked hypotheses unless validated. Teacher intervention taxonomy is descriptive with free-form fallback; failed explanations are preserved. Learning vs assessment and immediate/retention/generalization/transfer remain separate. DDS supplies mathematical play evidence, not School trading authority; hidden-information optimum != automatic human error. Tournament evidence respects missing trading/play and identity limits. Confidence only explicit/reliably elicited; latency only with reliable boundaries; temporal order != causality.

## Generated/derived products
FAQ, errors, contrast/minimal-change cases, teaching episodes, knowledge/canon/curriculum coverage, bottleneck hypotheses, diagnostic deals, test sets, LearningDebt/Gap queues, compressed knowledge views and course variants. Generated items pin exact source versions and never become historical evidence by generation.
Once normative Curriculum exists, candidate variants may include full multi-year, accelerated, individual, group, revision, tournament-prep and AI-teacher paths without changing verified canon.

## Teaching effectiveness / Student Model
Reconstruct explanation -> attempts -> intervention -> independent application -> delayed retention -> transfer; output for Diana is hypothesis, general methodology needs cross-student validation + owner. Digital Student Model remains read-only, pre-registers predictions and scores outcomes by skill/topic/novelty/help. Production profile/identity writes R3.

## Optimized processing
A INVENTORY -> CHRONOLOGY -> duplicates -> transcript coverage -> source QC.
B transcript-first segmentation/extraction.
C selective multimodal escalation for missing visual/cards/speaker/high-value evidence.
D consolidation/linking: knowledge/canon dedupe+scope+conflict; historical curriculum; topic normalization; vertical spirals; prerequisite hypotheses; student/teacher/outcome links; gaps/debt; retrieval index.
E Curriculum Builder: distinguish core progression vs remediation/repetition/enrichment -> infer candidate stage boundaries -> build Module Cards -> coverage/gap/conflict audit -> Candidate School Curriculum.
F QC deterministic provenance/completeness first, critic second, owner for R4/canon/curriculum activation.
G outputs graph, Candidate Curriculum, Candidate Student projection, Teacher brief, Learning Memory, retrieval views, review queue.

## Incrementality
Extraction keyed by source revision/hash + module/version + segment; consolidation adds normalized identity+scope. New video appends T(n+1); only impacted graph/modules/projections recompute. Canon/Knowledge/Curriculum corrections append immutable versions. Semantic index rebuild independent from authority graph.

## Knowledge/Curriculum health
Track duplicate rate, conflict count/age, provenance completeness, candidate->verified, rejected repeats, Knowledge/Curriculum Gap coverage, stale dependencies, orphan versions/modules, downstream usage, corrections, retrieval misses, review age, NEEDS_REVALIDATION impact, module coverage completeness and cost/time per verified addition. No arbitrary thresholds without evidence/owner policy.

## Multi-student future
Diana is first longitudinal reference, not universal truth. Other students validate/generalize difficulty, prerequisite edges, stage boundaries, bottlenecks, transfer expectations and teaching-effectiveness hypotheses; use controlled aggregates and do not rank people.

## Learning Loop
Historical T0...Tn reconstruction -> Candidate Curriculum. Prospective: evidence snapshot -> unresolved skill/knowledge/curriculum/transfer question -> diagnostic task -> pre-registered prediction -> response -> validation -> Candidate profile/knowledge/curriculum evidence -> teaching proposal -> delayed check -> META evaluation -> Learning Memory/Knowledge consolidation -> retrieval/gap/curriculum feedback.

## Autonomy
A1 may inventory/extract/normalize/link, build gaps and Candidate Curriculum in isolated evidence space. Normative School Curriculum activation, stage/sequence changes as School policy, trading-system changes and methodology changes are R4/owner-controlled. Production Student/profile/identity writes remain R3. Technical deterministic R1 fixes use A2 only per component authority.
