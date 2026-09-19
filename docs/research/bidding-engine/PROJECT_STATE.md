# School Canonical Bidding Engine — Durable Project State

Status: ACTIVE / P0
Primary tracker: GitHub issue #609
Research registry: Neon `assistant_lab.research_job`, key `BIDDING-ENGINE-P0-SINGLE-2026-08-26`

## Purpose
Build one school-owned bidding algorithm that can model an auction from the acting player's hand and public auction state, using the School Canon as authority and World / External Knowledge as research support.

## Governance v1 classification
- Canonical governance: `docs/governance/SCHOOL_GOVERNANCE_SYSTEM_V1.md`.
- Work class: `STRATEGIC`.
- Urgency: `NORMAL`.
- Strategic rank: `S1`.
- Governance mode: `ASSURED`.
- Minimum independent assurance: `I2`.
- Technical owner / Coordinator: AI Management System.
- Research Curator: logically separate provenance/evidence pass.
- Observatory: read-only research/runtime measurements where feasible.
- Red Team: independent falsification pass; use a different model, solver, formal checker or external engine for material conclusions.
- Director escalation: only material unresolved bridge-canon ambiguity, new paid spend, owner-only action, external obligation or strategic business choice.

## Durable role split
- School director / bridge expert: owner decides bridge-domain canon where expert judgment is genuinely required.
- Technical + research owner: ChatGPT owns architecture, databases, code, tooling, experiments, infrastructure, benchmarks, integration and technical decisions.
- Research Lab: an R&D execution layer inside the bidding-engine program, not the owner of the whole project. It receives bounded research questions, runs reproducible experiments/compute, and returns evidence, traces, measurements and confidence.
- Research Lab must not silently promote external findings into SCHOOL CANON and must not become an independent competing architecture.
- ChatGPT integrates laboratory evidence into the single school bidding engine and only escalates to the owner for bridge-canon decisions or new paid spend/resources.

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
11. Research Lab is used actively for world-research, BEN/BBA/Pons/Bridgit analysis, DDS/Monte-Carlo/mass compute, benchmarking and evidence generation; the main engine architecture remains centrally owned by ChatGPT.

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

## Research Lab contract
A lab task should have:
1. a concrete research question;
2. pinned inputs/version/model/commit where relevant;
3. a reproducible experiment or analysis method;
4. bounded compute/scope;
5. explicit output evidence;
6. conclusion + confidence + known limitations;
7. a clear link to a pending architecture/knowledge decision.

Default flow:
`research question -> experiment -> evidence -> conclusion/confidence -> ChatGPT technical decision -> optional bridge-expert escalation`

Avoid open-ended research with no decision target.

## Current engineering objective
Design a common machine-readable knowledge object and retrieval semantics that can represent both SCHOOL CANON and WORLD / EXTERNAL KNOWLEDGE while preserving authority separation.

## 2026-08-29 draft extension — WORLD knowledge runtime

Draft branch `bidding/world-knowledge-v0` adds a storage-neutral two-lane
resolver contract and a forward migration candidate `0201_world_knowledge_v0`.
It is **not applied to production**, creates no bidding rule and has no canon
activation effect.  The resolver records `CANON_GAP` before any WORLD lookup;
it stops on `CANON_CONFLICT`, preserves `WORLD_CONFLICT`, and never promotes a
WORLD result.  Robot decisions are explicitly pinned and hidden-information
guarded.  The exact current primary-source gate remains unchanged: `0200` / #798
must first pass the independent I2 review and promotion procedure.

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
1. Read `docs/governance/SCHOOL_GOVERNANCE_SYSTEM_V1.md` when governance affects the task.
2. Read this file.
3. Read current GitHub issue #609 and its latest comments.
4. Query the Neon research row keyed `BIDDING-ENGINE-P0-SINGLE-2026-08-26` if current execution state matters.
5. Read only the specific research/design files needed for the task.
6. Do not reconstruct project state from conversational memory when durable project evidence exists.

After any material research/design/implementation decision:
- update this file if the durable state/invariants/next milestone changed;
- record detailed evidence in a dedicated research/design file or issue comment;
- keep #609 as the high-level tracker;
- keep large/raw evidence outside this file.

## Change discipline
This file should remain short enough to read at the beginning of every related task. Detailed findings belong in sibling files such as `research/`, `design/`, `benchmarks/`, or linked issue/PR evidence.
