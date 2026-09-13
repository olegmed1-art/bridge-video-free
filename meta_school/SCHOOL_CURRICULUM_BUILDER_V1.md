# SCHOOL CURRICULUM BUILDER v1.0

Status: ARCHITECTURE_DEFINED / CANDIDATE_CURRICULUM_ONLY
Primary historical corpus: Diana longitudinal lessons (~250 videos) + verified written School materials + validated Knowledge Graph.

## Goal
Build a complete School sports-bridge course from zero to advanced/tournament level, organized by themes and years/stages of learning, while preserving the distinction between:
1. what Diana actually studied and when;
2. what the School currently teaches as verified canon;
3. what META infers may be a better curriculum sequence.

The historical Diana sequence is evidence, not automatically the normative curriculum.

## Three curriculum layers
### HISTORICAL_CURRICULUM
Reconstruct exact lesson chronology: topics introduced, revisited, exercises, help, student outcomes and actual calendar/learning year.

### STRUCTURED_CURRICULUM_GRAPH
Normalize historical topics into stable Topic/Skill/Canon/Knowledge nodes, vertical difficulty ladders and prerequisite hypotheses.

### CANDIDATE_SCHOOL_CURRICULUM
Proposed School sequence from beginner to advanced. Requires owner approval before becoming normative School Curriculum.

## Dual time axes
Each topic/module stores:
- DianaHistoricalYear/Stage: when it actually occurred;
- ProposedSchoolYear/Stage: recommended placement in Candidate Curriculum.
Moving a topic between years is a curriculum proposal, not a historical correction.

## Course hierarchy
Program -> Year/Stage -> Domain -> Module -> Topic -> Subtopic -> LearningObjective -> Opportunity/Exercise -> Assessment/Transfer.

Years are not hard-coded before corpus analysis. Natural stages are inferred from chronology, prerequisite graph, complexity, revisits and independence/transfer evidence, then proposed for owner review.

## Domains
Initial domain candidates, to be confirmed/expanded from School evidence:
- основы игры и счёт;
- торговля;
- конкурентная торговля/интервенция;
- розыгрыш;
- защита;
- первый ход;
- техника игры;
- планирование;
- турнирная практика;
- другие фактически обнаруженные направления.
Domain taxonomy itself is versioned Candidate Curriculum knowledge, not imposed external canon.

## Vertical spirals
Recurring themes are modeled as one concept with progressive levels rather than duplicate unrelated topics. Example pattern:
Topic I foundations -> Topic II application -> Topic III complex/competitive -> Topic IV tournament/transfer.
Exact levels and contents are derived from School evidence.

## Module Card
Every curriculum module links, where evidence exists, to:
- prerequisites;
- learning objectives;
- exact VERIFIED_CANON versions;
- VERIFIED_KNOWLEDGE versions;
- School terminology;
- teacher explanations/variants;
- examples/counterexamples/exceptions;
- typical errors/misconceptions;
- diagnostic and teaching deals;
- exercises and homework;
- learning opportunities;
- immediate understanding check;
- independent assessment;
- delayed retention check;
- generalization task;
- transfer/tournament evidence;
- source videos/timestamps/documents;
- known gaps/conflicts;
- difficulty/complexity evidence;
- downstream modules.
Missing elements remain explicit Knowledge/Curriculum Gaps; they are not invented.

## Entry and exit profiles
Each Year/Stage has Candidate EntryProfile and ExitProfile expressed as observable capabilities/knowledge, not a single score. Exit may require independent performance, retention and/or transfer only where supported/approved. Thresholds are not invented.

## Historical-to-course transformation
1 INVENTORY/CHRONOLOGY of lessons.
2 Extract topics/canon/knowledge/opportunities/outcomes.
3 Normalize aliases and duplicate topic names.
4 Build Topic Genealogy and vertical spirals.
5 Build prerequisite hypotheses from teaching order + demonstrated dependencies.
6 Detect revisits caused by individual Diana needs vs planned progression.
7 Separate core sequence from remediation/repetition/enrichment.
8 Identify natural learning stages/year boundaries.
9 Build Candidate modules and year placement.
10 Coverage/gap/conflict audit.
11 Validate against written School canon and knowledge base.
12 Owner review -> normative School Curriculum version.

## Individual-path protection
A repeated topic in Diana's history may mean curriculum importance, planned spiral, forgetting, remediation, misconception or chance. The builder must classify evidence before using repetition to determine course hours/order.
Diana-specific remediation is not automatically copied into the general course.

## Difficulty model
Difficulty remains multidimensional: prerequisite depth, decision complexity, number of competing rules, information load, novelty, help required, retention/generalization/transfer evidence and later cross-student performance. No single difficulty score is authoritative.

## Course variants from one graph
Once normative curriculum exists, the same graph may generate Candidate variants for:
- full multi-year course;
- accelerated course;
- individual lessons;
- group course;
- revision/returning-player course;
- tournament preparation;
- AI-teacher pathway.
Variants cannot change verified canon; they select/order modules under explicit constraints.

## Assessment architecture
Separate teaching tasks from assessment tasks. Suggested progression where appropriate:
learned example -> independent same-principle task -> delayed retention -> minimally changed generalization -> unfamiliar transfer -> tournament evidence.
Assessment result updates Student Evidence, not Canon.

## Curriculum optimization evidence
META may propose sequence/spacing/exercise changes using bottlenecks, learning debt, repeated misconceptions, weak transfer, prerequisite conflicts, retrieval gaps and cross-student outcomes. Such proposals remain CANDIDATE_CURRICULUM/R4 until owner approval.

## Links to Knowledge Acquisition
Curriculum Builder consumes exact CanonVersion/KnowledgeVersion references. Missing content creates KnowledgeGap. Retrieval misses during module building feed the gap queue. New verified knowledge may close a gap and trigger only impacted module revalidation.

## Links to Teacher Model
Observed effective/ineffective explanations are attached as evidence to modules. They do not become mandatory teaching method automatically.

## Links to Student Model
Diana provides first longitudinal validation history. Later students validate/generalize stage boundaries, difficulty, prerequisites and transfer expectations. Do not rank students.

## Versioning
Historical Curriculum is immutable evidence projection.
Candidate Curriculum versions are immutable proposals.
Normative School Curriculum requires owner activation and has valid-from/supersedes lineage.
Generated lesson plans pin exact CurriculumVersion + Canon/Knowledge versions.

## Completion outputs
- complete Topic Map;
- historical Diana curriculum by year/date;
- vertical topic spirals;
- prerequisite graph;
- Candidate years/stages;
- module cards;
- Entry/Exit profiles;
- coverage/gap/conflict map;
- Candidate School Curriculum from zero to advanced;
- source/evidence map for every module.

## Autonomy
Extraction, normalization, graph construction, gap detection and Candidate Curriculum generation: A1/read-only toward canon and production Student state.
Activation of normative School Curriculum, changes to trading system or teaching methodology: R4/owner-controlled.