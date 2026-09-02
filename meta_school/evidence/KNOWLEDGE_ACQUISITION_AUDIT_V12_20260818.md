# Knowledge Acquisition & Consolidation — v1.2 Audit
Date: 2026-08-18

## Checks
Knowledge vs Canon separation: PASS.
KnowledgeItem vs KnowledgeVersion separation: PASS.
Evidence provenance gate: PASS.
Exact/near duplicate handling: PASS.
Scope-aware contradiction handling: PASS.
Historical/superseded retention: PASS.
Gap detection/closure lineage: PASS.
Generated-content contamination protection: PASS.
Downstream exact-version provenance: PASS.
External literature cannot override School canon: PASS.
Incremental/idempotent consolidation: PASS.
Knowledge-base health metrics: PASS.

## Further useful additions identified and integrated
1. Scope normalization before conflict detection — prevents false conflicts between rules valid in different contexts.
2. Authority-first retrieval — prevents frequently repeated weak evidence outranking verified canon.
3. KnowledgeVersion downstream usage tracking — enables impact analysis before correcting/superseding knowledge.
4. Orphan-version detection — finds validated knowledge that is unreachable/unusable by curriculum/materials.
5. Retrieval-miss logging — turns failed searches during lesson/material generation into candidate Knowledge Gaps.
6. Rejected-candidate memory — prevents META from repeatedly proposing the same disproved knowledge.
7. Validation-basis field — distinguishes solver proof, owner canon, source corroboration, deterministic document match, etc.
8. Known-limit/exception field — prevents a generally valid item from being applied outside scope.
9. Dependency staleness — revalidates only knowledge affected by changed canon/source/algorithm rather than aging everything.
10. Impact-aware correction — before superseding a widely used KnowledgeVersion, enumerate downstream materials/skills/curriculum references.

## Additional future opportunities (do not require architecture change now)
- multilingual aliases/terminology mapping if School materials later use multiple languages;
- semantic search embeddings as a retrieval index only, never authority source;
- cross-student anonymized misconception frequency once enough students exist;
- active-learning prioritization: review candidates that close high-use gaps or resolve high-impact conflicts first;
- knowledge compression: generate concise summaries from exact version references while retaining provenance.

## Verdict
KNOWLEDGE_ACQUISITION_ARCHITECTURE = PASS
CANON_SAFETY = PASS
DEDUP/CONFLICT/GAP LOOP = PASS
RETRIEVAL/IMPACT LOOP = PASS
READY_FOR_CORPUS_INVENTORY/PILOT = YES.