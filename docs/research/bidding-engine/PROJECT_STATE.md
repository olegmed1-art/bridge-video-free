# School Canonical Bidding Engine — Durable Project State

Status: ACTIVE / P0
Primary tracker: GitHub issue #609
Research registry: Neon `assistant_lab.research_job`, key `BIDDING-ENGINE-P0-SINGLE-2026-08-26`

## Purpose
Build one school-owned bidding algorithm that can model an auction from the acting player's hand and public auction state, using the School Canon as authority and World / External Knowledge as research support.

## Durable invariants
1. There is one target bidding engine, not multiple competing school engines.
2. SCHOOL CANON is the authoritative bidding knowledge base of the school.
3. WORLD / EXTERNAL KNOWLEDGE is a separate auxiliary knowledge base for BEN, BBA/EPBot, Pons, Bridgit, literature and other external evidence.
4. External knowledge is never silently promoted into SCHOOL CANON.
5. Runtime decisions must use only the acting player's information set. Hidden partner/opponent cards cannot establish canonical facts.
6. If school canon is insufficient, expose a knowledge gap instead of inventing a school rule.
7. The knowledge representation must be machine-readable and allow structural comparison/linking between canonical and world knowledge.
8. Explanation is required on demand, but human-facing document formatting is not a storage constraint.
9. Existing GitHub, Neon, Drive and already-provisioned compute may be used autonomously. New paid tools/features/resources require owner approval before spend.
10. Current scope is bidding first. Defense and declarer play are future extensions unless a shared infrastructure choice clearly benefits bidding.

## Current known infrastructure
- Production Neon project: `bridge-school-core` (`misty-poetry-18012774`).
- Existing school knowledge tables include `skill`, `knowledge_item`, `knowledge_version`, `canon_activation`, `source`, `ai.knowledge_fact`, `ai.system_rule`, agreement tables, course tables and research tables.
- Existing `skill` data contains a substantial L1 bidding model but lacks complete provenance linkage for most skills.
- `agreement_set`, `agreement_version`, `agreement_activation` were observed empty during audit.
- `ai.knowledge_fact` / `ai.system_rule` currently contain only a small formalized subset.
- A previously inspected course notes file was explicitly rejected by the owner as canonical knowledge and must not be treated as school canon unless the owner later explicitly reverses that decision.

## Research sources currently prioritized
- BEN — neural bidding policy, sampling, rollout, DDS/SD evaluation, BBA integration, accessible models/weights.
- Pons — auction trie, Rules/Constraint DSL, projection/inference, fallback, provenance, measurement.
- Bridgit — explicit BidRule/Constraint/BiddingState rule engine.
- BBA/EPBot — permitted API/binary behavior and decision-surface reconstruction.
- Wider bridge literature and open engines including GIB/BIDI/PIDM and neural bidding research.

## Required knowledge layers
### SCHOOL CANON
Authoritative school bidding knowledge with explicit provenance/version/approval/activation/tests.

### WORLD / EXTERNAL KNOWLEDGE
Machine-readable auxiliary world knowledge with source family and provenance class (explicit, reconstructed, empirical, learned, literature-derived, etc.).

### Evidence / research traces
Decision traces, benchmarks, model hashes, experiment outputs and reconstruction evidence. Evidence supports knowledge but is not itself canon.

## Current engineering objective
Design a common machine-readable knowledge object and retrieval semantics that can represent both SCHOOL CANON and WORLD / EXTERNAL KNOWLEDGE while preserving authority separation.

The target runtime flow is conceptually:
`hand + public auction + context -> knowledge retrieval -> applicable rules/inferences -> bid selection -> optional explanation -> decision trace`

The exact physical split between Neon, Git and Drive is intentionally not frozen. It should be chosen from evidence and operational convenience.

## Immediate milestone M0
1. Reproducible end-to-end BEN bid trace.
2. Deep Pons, Bridgit and BBA/EPBot analysis.
3. Evidence-based architecture for the single school bidding engine.
4. v0 common machine-readable knowledge object.
5. Runtime resolver contract.
6. Canonical <-> world comparison/linking semantics.
7. One bounded school bidding branch implemented end-to-end: source -> canon -> resolver -> bid -> optional explanation -> trace -> tests.

## Continuity protocol
This file is the durable handoff point across chats.

At the start of any new chat/task about the bidding engine:
1. Read this file first.
2. Read current GitHub issue #609 and its latest comments.
3. Query the Neon research row keyed `BIDDING-ENGINE-P0-SINGLE-2026-08-26` if current execution state matters.
4. Read only the specific research/design files needed for the task.
5. Do not reconstruct project state from conversational memory when durable project evidence exists.

After any material research/design/implementation decision:
- update this file if the durable state/invariants/next milestone changed;
- record detailed evidence in a dedicated research/design file or issue comment;
- keep #609 as the high-level tracker;
- keep large/raw evidence outside this file.

## Change discipline
This file should remain short enough to read at the beginning of every related task. Detailed findings belong in sibling files such as `research/`, `design/`, `benchmarks/`, or linked issue/PR evidence.
