# School Governance Operating Model

Status: **ADOPTED / IMPLEMENTATION STARTED 2026-08-26**

This document formalizes the school-wide governance pattern previously used in bounded experiments and aligns it with the School governance v2.0 and Technical Governance.

## 1. Core ownership

- **School Director / Bridge Expert** — sets strategic goals, owns the school, and resolves genuinely material unresolved bridge-domain/canon questions.
- **AI Technical & Research Owner** — owns implementation: architecture, software, databases, research, pedagogy systems, operations, reliability, cost engineering, evidence integration and continuous improvement.

Routine technical and research work is not delegated back to the Director.

## 2. Independent control functions

These are **functions, not necessarily separate people**. One AI system may instantiate them as separate bounded roles, agents, jobs or review passes, but their evidence and decision responsibilities must remain logically separated.

### COORDINATOR
Purpose: keep the work coherent and bounded.

Responsibilities:
- define/freeze scope, objective, WIP and decision target;
- coordinate dependencies and allowed next actions;
- maintain stop/go state and terminal classification;
- prevent silent scope expansion;
- integrate evidence only after independent functions report.

The Coordinator does not manufacture evidence and does not overrule failed evidence gates by narrative judgment.

### RESEARCH CURATOR
Purpose: protect the research question and provenance from hindsight bias and evidence drift.

Responsibilities:
- freeze hypothesis, evaluation criteria and source/version identities before material execution where applicable;
- register sources, model/commit/config hashes, assumptions and known limitations;
- preserve raw/primary evidence and distinguish observation from interpretation;
- verify that a conclusion answers the original question rather than a rewritten easier question;
- maintain provenance and evidence completeness.

The Curator does not decide success by changing criteria after seeing results.

### RED TEAM
Purpose: independently try to falsify the proposed conclusion and find hidden failure modes.

Responsibilities:
- attack assumptions, edge cases, missing evidence and hidden-information leakage;
- test failure paths, negative cases, contradictory evidence and regression risk;
- search for false-positive PASS/COMPLETED states;
- test whether the same conclusion survives alternative explanations and adversarial inputs;
- explicitly report unresolved risks and counterexamples.

The Red Team should not optimize for agreement with the Coordinator or implementer.

### SCHOOL OBSERVATORY
Purpose: provide read-only factual measurement of what actually happened.

Responsibilities:
- collect time, cost, bytes, retries, versions, health, latency, quality metrics and other task-relevant observables;
- maintain current-state and longitudinal measurements;
- distinguish baseline from attributable change;
- record terminal evidence without interpreting bridge canon;
- provide evidence usable by Coordinator, Curator, Red Team and Director.

The Observatory is preferably read-only with respect to the system under evaluation.

## 3. Specialist / independent experts

Specialist experts are invoked when a task benefits from a distinct method or authority. Examples include:
- Research Lab;
- BEN / BBA / Pons / other bridge engines as external research experts;
- DDS3 as a double-dummy computational authority within its defined boundary;
- database/reliability/security/FinOps specialists or automated analyzers;
- independent bridge-domain reference checks.

Specialist outputs are evidence/advice, not automatic canon and not automatic production promotion.

## 4. Standard strategic procedure

For a material research, architecture, reliability, pedagogy-system or algorithmic change:

`Director goal -> Coordinator scope -> Curator frozen question/evidence contract -> Research/Implementation -> Observatory measurements -> Red Team falsification -> Evidence Gate -> AI Technical Owner decision -> Director escalation only if required`

### Stage A — Intent
- state the actual decision to be made;
- define success/failure/inconclusive/stop conditions where appropriate;
- identify owner-only or paid boundaries.

### Stage B — Evidence contract
- pin relevant sources, versions, inputs, baseline and metrics;
- separate facts, hypotheses and future claims;
- define what evidence is required before promotion.

### Stage C — Execution / research
- Research Lab and technical workers perform bounded work;
- checkpoints and provenance are retained;
- no silent scope expansion.

### Stage D — Independent observation
- Observatory records actual state and measurements;
- primary system state overrides stale documentation or chat memory.

### Stage E — Adversarial review
- Red Team attempts to disprove the candidate conclusion;
- missing evidence, leakage, regressions and alternative explanations are surfaced.

### Stage F — Decision
- PASS/FAIL/INCONCLUSIVE/STOPPED or task-specific decision is based on evidence;
- production/canon/methodology promotion is a separate decision when the evidence boundary does not justify it;
- Director is asked only for bridge-canon ambiguity, material spend, owner-only action, external obligation or strategic business choice.

## 5. Interaction rules

1. **Curator and Red Team are independent of the implementer's desired outcome.**
2. **Observatory reports measurements, not advocacy.**
3. **Coordinator cannot turn missing evidence into PASS.**
4. **A successful technical run does not imply domain correctness, pedagogical benefit or canonical approval.**
5. **Evidence dimensions remain separated** when a project has multiple layers (execution, artifact integrity, domain analysis, methodology, publication, promotion, cost, etc.).
6. **Actual state beats documentation.** Reconcile primary sources before material action.
7. **Append-only evidence is preferred for frozen experiments.** Hypotheses and gates are not silently rewritten after results.
8. **Independent functions may be implemented by separate AI passes/agents rather than separate humans**, but the roles and outputs must remain distinct and attributable.

## 6. When the full model is required

Use the full Coordinator + Curator + Red Team + Observatory model for:
- P0/P1 research or infrastructure experiments;
- new core algorithms or major architecture changes;
- canon-affecting research where evidence may later inform school knowledge;
- production migrations and reliability/recovery proofs;
- costly or difficult-to-reverse changes;
- benchmark claims used to select or promote an engine/model;
- cases with meaningful hidden-information, privacy, provenance or evaluation risk.

For small reversible maintenance, roles may be collapsed into a lighter workflow while preserving evidence and independent checks proportionate to risk.

## 7. Research Lab relationship

The Research Lab is an execution and evidence-producing capability, not the owner of school strategy or architecture.

Default lab flow:
`Research question -> pinned experiment -> evidence -> conclusion + confidence -> Curator provenance check -> Red Team challenge -> Technical Owner integration`

The Lab may use existing compute, GitHub, Neon, Drive and free/open tools autonomously within standing governance. New paid resources require Director approval.

## 8. Canon and world knowledge relationship

- SCHOOL CANON remains the authoritative school knowledge contour.
- WORLD / EXTERNAL KNOWLEDGE remains independent research/reference evidence.
- Research may create candidates and links between the two, but external knowledge is not silently promoted.
- Material unresolved bridge ambiguity is escalated to the Director as bridge expert.

## 9. School-wide adoption

This operating model is intended to apply across major school workstreams, including:
- bidding engine and knowledge infrastructure;
- tournament analysis;
- video-processing and lesson-analysis pipelines;
- research and robot evaluation;
- database/reliability/recovery work;
- pedagogical-system changes;
- significant financial or capacity experiments.

Existing project-specific gates remain in force until individually re-audited; this governance model does not silently remove safety restrictions.

## 10. Evidence lineage

This model formalizes and generalizes the role pattern previously used in GitHub experiment #547 and repeated in #562:
- Coordinator held WIP/scope/stop-go/final outcome;
- Research Curator froze hypothesis and criteria;
- Red Team independently tested missing result, REVIEW, hash mismatch and deferred stages;
- School Observatory recorded read-only time/bytes/cost/retries/evidence;
- the roles were explicitly described as functions rather than four mandatory people.

It is also consistent with School governance v2.0 and `docs/TECHNICAL_GOVERNANCE.md`, which delegate routine technical/research implementation to the AI operator and require evidence-driven, reversible, observable changes.
