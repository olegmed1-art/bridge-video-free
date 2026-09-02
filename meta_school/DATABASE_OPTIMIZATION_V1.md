# DATABASE OPTIMIZATION v1.0 — Longitudinal Knowledge/Curriculum

## Principle
Do not create a second graph database or duplicate the existing Neon model. Reuse current normalized entities and represent new architecture primarily through types, versions, links and metadata until pilot evidence proves a dedicated table is necessary.

## Existing tables reused
SourceAsset -> `source`, `asset`, `source_asset`, `media_asset`, `transcript`, `transcript_segment`.
EvidenceSpan -> `evidence` + `evidence_link`.
LessonSession/Episode -> `session`, `learning_interaction`, `episode`.
KnowledgeItem/Version -> `knowledge_item`, `knowledge_version`, `knowledge_version_source`, `knowledge_relation`.
Canon -> existing agreement/canon activation + knowledge versions with explicit authority/scope; no parallel canon table introduced by longitudinal pilot.
KnowledgeGap/CurriculumGap/RetrievalMiss -> `knowledge_gap` + candidate solutions; distinguish subtype in context_scope/typed metadata initially.
Topic/Skill -> `topic`, `skill` and linking tables.
Curriculum -> `course`, `course_version`, `course_topic`, `course_skill`; stage/year/module hierarchy remains versioned `course_version.curriculum` + topic hierarchy/metadata during pilot rather than new stage/module tables.
Exercises/assets -> `exercise`, `exercise_version`, topic/skill links; reusable non-exercise assets use `knowledge_item` typed categories and artifact/media links.
Student longitudinal observations -> existing decision/error/success/exercise-attempt/skill-assessment/student-profile projection pipeline; no duplicate StudentResponse store until a demonstrated gap.
Dependencies/impact -> `dependency_edge`, invalidation tables and version relations.
Operational events -> `domain_event`, analysis_run/input/output, quality tables.

## Reusable asset mapping
Represent `ReusableKnowledgeAsset` as `knowledge_item.knowledge_type` categories plus versioned `knowledge_version.content`, with exact `evidence_link` provenance. This avoids a new table for every asset class. Frequently queried structural fields may be promoted to columns only after pilot query evidence.

## Confidence model
Do not add five nullable columns everywhere. Store source-level quality in evidence/source entities, extraction/speaker confidence in transcript/evidence metadata, and authority/review in knowledge_version. Keep dimensions normalized at their owning layer.

## Versioning
Never update historical content in place. `knowledge_version`, `course_version`, `exercise_version`, artifact versions and version_relation carry immutable evolution. Current projections/activation tables point to active versions.

## Deduplication
Use stable keys for exact conceptual identity. Source/asset hashes and existing uniqueness constraints prevent duplicate files. Semantic near-duplicate detection creates candidate relations, not destructive merges. One KnowledgeItem can have many Evidence links.

## Search
Phase 1: existing relational indexes + exact stable keys + topic/type/authority filters.
Phase 2 only after corpus pilot: optional semantic embedding index maintained separately from authority graph. Embeddings never determine authority.
Avoid premature GIN/vector indexes before measuring actual retrieval workload.

## Hot-path indexes added in lab
- episode by session + sequence;
- evidence by school + evidence type + recency;
- course topics by course version + sequence;
- knowledge gaps by school + status + priority + recency.
These complement existing transcript time/speaker, knowledge type/version/authority, evidence source/asset and knowledge relation indexes.

## Storage tiers
Tier A immutable/high-value: source identity, provenance, transcript revision, evidence, verified/candidate versions, decisions.
Tier B structured reusable: episodes, topic/skill links, curriculum links, reusable assets, gaps, outcomes.
Tier C regenerable: summaries, briefs, rankings, semantic indexes, temporary projections. Regenerate rather than duplicate authoritative content.
Large video bytes remain in Drive/object storage references, not Postgres.

## 250-video scaling rules
Transcript text is stored once per transcript segment. Episode/knowledge objects reference segments/evidence rather than copying transcript passages repeatedly. Course modules reference exact Knowledge/Canon versions rather than embedding full rule text. Student projections reference observations; raw evidence is not copied into every snapshot. New extraction versions only recompute impacted source segments and dependency subgraphs.

## Data-retention rule
Do not delete original source/evidence/history. Regenerable caches/projections may be rebuilt. Rejected candidates are retained compactly to prevent repeated failed work.

## Pilot gate before further schema growth
Before adding dedicated tables for ReviewQueue, RetrievalEvent, CurriculumStage/Module, TerminologyAlias or ReusableAsset, process a chronological pilot and measure query patterns/cardinality. Add a dedicated relation only if JSON/type-based representation creates integrity, query-performance or lifecycle problems.

## Expected result
Lower schema growth, fewer joins duplicated across parallel models, less repeated text/storage, cheaper reprocessing, simpler provenance and safer version invalidation while preserving the existing Neon system of record.