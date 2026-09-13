# KNOWLEDGE ENRICHMENT & REUSABLE ASSETS v1.0

Status: ARCHITECTURE_DEFINED
Purpose: extract reusable School value even when an episode contains no new canon/rule.

## Reusability Scan
Every processed Episode receives a lightweight Reusability Scan after provenance/QC.
Decision classes:
- NEW_KNOWLEDGE: genuinely new candidate knowledge;
- ENRICH: adds useful asset/evidence to an existing Knowledge/Canon/Curriculum item;
- DUPLICATE: no meaningful new reusable value beyond evidence support;
- CONFLICT: materially incompatible claim/asset requiring review;
- GAP: demonstrates missing knowledge/explanation/example/exercise/assessment;
- NO_REUSABLE_ASSET: valid episode but no reusable School asset.

NO_REUSABLE_ASSET is a normal outcome; extraction must not manufacture value.

## ReusableKnowledgeAsset
Reusable assets are versioned objects linked to exact source Evidence and exact KnowledgeVersion/CanonVersion/Skill/CurriculumModule where applicable.
Asset types include:
- EXPLANATION;
- ALTERNATIVE_EXPLANATION;
- ANALOGY;
- EXAMPLE;
- COUNTEREXAMPLE;
- EXCEPTION_EXAMPLE;
- TEACHING_DEAL;
- DIAGNOSTIC_DEAL;
- EXERCISE;
- HOMEWORK;
- CHECK_FOR_UNDERSTANDING;
- RETENTION_CHECK;
- GENERALIZATION_CASE;
- TRANSFER_CASE;
- STUDENT_QUESTION;
- FAQ_ANSWER_CANDIDATE;
- TYPICAL_ERROR;
- MISCONCEPTION_EXAMPLE;
- KNOWLEDGE_CONFLICT_EXAMPLE;
- TEACHER_QUESTION;
- INTERVENTION_EPISODE;
- MOMENT_OF_INSIGHT;
- FAILED_EXPLANATION;
- AMBIGUOUS_FORMULATION;
- NEGATIVE_KNOWLEDGE;
- TERMINOLOGY_EXAMPLE;
- TOURNAMENT_EXAMPLE;
- DDS_VALIDATED_PLAY_EXAMPLE;
- CONTRAST_PAIR_MEMBER;
- MINIMAL_CHANGE_CASE.

Asset is not authority. It inherits/links the authority of the knowledge it illustrates but cannot promote that knowledge.

## ENRICH instead of duplicate
If a rule already exists, a new video may enrich it with a better example, new exception evidence, common error, question, exercise or explanation. The canonical/knowledge item remains single; assets accumulate around it with provenance.

## Negative Knowledge
Store evidence-grounded patterns that should not be repeated as if correct: disproved claim, misleading/ambiguous wording, known invalid example, recurring incorrect interpretation, failed candidate, unsafe overgeneralization. NegativeKnowledge must state what is rejected, evidence/basis, scope and whether rejection is canonical, mathematical, factual or pedagogical-hypothesis level.
A failed teaching episode is not automatically a universally bad method.

## Explanation library
For each concept maintain explanation variants rather than a single winner. Track source, context, learner stage, prerequisite assumptions, interventions around it and later Outcome evidence.
`BestExplanationCandidate` is a ranking/proposal only. Frequency or one successful outcome cannot establish causal superiority.

## Reuse Evidence
Each later use of an asset creates UsageEvent linked to context, learner/stage, purpose and subsequent outcome where available. This permits evidence such as diagnostic usefulness or repeated comprehension support without equating reuse frequency with effectiveness.

## Asset quality dimensions
Keep separate: provenance completeness, canon/knowledge alignment, scope fit, clarity evidence, diagnostic usefulness evidence, transfer usefulness evidence, reuse count, outcome support and conflict status. Do not collapse into one authoritative score.

## Contrast/Minimal-change assembly
Assets may be linked into ContrastSet when superficially similar situations require different decisions, or MinimalChangeSet when one controlled variable changes. The set records exactly what differs and which verified knowledge explains the change. Generated contrast sets remain derived products.

## Promotion into Curriculum
Reusable assets may be proposed for a Curriculum Module when they match exact module Canon/Knowledge versions and stage/scope. Inclusion in normative School Curriculum is owner-controlled where it constitutes methodology/curriculum policy; technical attachment/indexing may remain Candidate work.

## Search/retrieval
Retrieval can answer not only `what is the rule?` but also `how has this been explained?`, `give a counterexample`, `what errors occur?`, `how can I test it?`, `show a transfer case`, `what failed before?`.
Student-facing canonical explanations must remain consistent with current verified canon and exact scope.

## Privacy
Identifiable student questions/episodes/clips remain protected. Reusable abstraction may be de-identified; raw clip reuse outside the original context requires appropriate permission.

## Processing integration
Episode -> provenance/QC -> Reusability Scan -> Knowledge Match -> NEW/ENRICH/DUPLICATE/CONFLICT/GAP/NONE -> ReusableKnowledgeAsset candidate -> dedupe/scope check -> link to exact versions -> usage/outcome tracking -> META/Knowledge Health.

## Health metrics
Track asset candidates, ENRICH ratio, duplicates suppressed, assets by type, orphan assets, conflicting assets, assets used downstream, usage events, assets with outcome evidence, negative-knowledge retrieval preventions and cost/time per reusable asset. Thresholds are not invented.

## Autonomy
A1 may extract/link Candidate reusable assets and NegativeKnowledge with evidence. Assets cannot activate School canon/methodology. Promotion into normative curriculum or declaring an explanation as required School method remains owner-controlled.