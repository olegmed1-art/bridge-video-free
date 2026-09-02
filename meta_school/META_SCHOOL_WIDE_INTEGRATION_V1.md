# META SCHOOL-WIDE INTEGRATION v1.0

Status: DEFINED / STATICALLY_VALIDATED / NOT_BULK_ACTIVATED

## Purpose
Connect each School component to META CLOSED LOOP without granting it more autonomy than that component has independently earned.

## Global rule
Integration is per-component, not global. Existing A2 for Video 3.1 FREE does not automatically grant A2 to DDS, tournament analysis, lesson/material generation, online school, Student Model, or shared infrastructure.

Canonical bidding system, teaching methodology and owner-approved pedagogical canon remain R4/OWNER_CONTROLLED.

## Component onboarding state machine
DISCOVER -> BASELINE -> CONTRACT -> ADAPTER -> OBSERVE_SHADOW -> CALIBRATE -> FAILURE_TESTS -> GATE_REVIEW -> A1_ENABLE -> A1_OBSERVE -> A2_ELIGIBILITY -> A2_CANARY -> A2_COMPONENT_ENABLE

Any failed/unknown/stale/conflicted gate -> BLOCKED/RETEST/OWNER_REVIEW as applicable.

## Stage 1 — DISCOVER
For the target component identify:
- ComponentID and current Stable/version identity;
- authoritative inputs/outputs;
- source/canonical references;
- dependencies and downstream consumers;
- current execution path and storage;
- existing tests/evidence/recovery;
- cost/time signals;
- semantic boundary: technical vs canonical/pedagogical.

If Stable cannot be identified, stop integration for that component.

## Stage 2 — BASELINE
Build a frozen baseline from successful and failed historical/real cases. Preserve selection rule and denominators. Missing evidence is UNKNOWN.

Minimum baseline categories where applicable:
- normal cases;
- known regression/error cases;
- edge/adversarial cases;
- no-change cases;
- owner/canonical-boundary cases.

## Stage 3 — COMPONENT CONTRACT
Create a component-specific META contract defining:
- target metrics;
- guardrails;
- coverage/completeness metric;
- acceptance criteria;
- risk mapping R0–R4;
- dependencies;
- recovery requirement;
- cost/time cap;
- validator independence policy;
- write/read-back strategy;
- forbidden actions.

Do not invent pedagogical success criteria not supported by School canon or real student evidence.

## Stage 4 — ADAPTER
Implement the smallest adapter that converts component executions into META events:
Input -> Run -> Result -> QC -> Corrections -> Cost -> Time -> Evidence.

Adapter requirements:
- idempotent RunID/EventID;
- provenance/source identity;
- Stable version pinning;
- no secret/raw credential persistence;
- no production mutation during Shadow;
- schema/version compatibility with downstream consumers.

## Stage 5 — OBSERVE_SHADOW
Run read-only Shadow observation first. META may classify ERROR/REGRESSION/INEFFICIENCY/IMPROVEMENT_OPPORTUNITY/NO_CHANGE but cannot promote.

## Stage 6 — CALIBRATE
Require multiple decision-path examples before autonomy increases:
- justified candidate;
- NO_CHANGE;
- REJECT over-broad/unsafe candidate;
- OWNER_REVIEW boundary;
- INCONCLUSIVE/RETEST where relevant.

## Stage 7 — FAILURE TESTS
Exercise or statically prove:
- stale Stable -> REBASE_REQUIRED;
- unknown external state -> reconcile/no blind retry;
- cost cap -> stop;
- dependency regression -> reject;
- validator non-independence -> gate fail;
- rollback/read-back failure -> rollback/freeze;
- risk downgrade attempt -> deny.

## Stage 8 — GATE REVIEW
A1 eligibility requires:
- Stable identifiable;
- adapter works;
- Evidence provenance works;
- Shadow decision calibration passes;
- failure-path gates pass;
- no production writes in Shadow;
- component-specific regression suite exists or deterministic equivalent is documented.

## Stage 9 — A1 ENABLE
A1 permits autonomous observation, diagnosis, Candidate creation and isolated sandbox/regression work for that component. No Stable promotion.

## Stage 10 — A2 ELIGIBILITY
A2 may be considered only for deterministic bounded R1 technical defects. Required:
- at least one successful A1 cycle;
- objective reproducible defect;
- minimal frozen Candidate;
- independent deterministic validation;
- bounded dependency impact;
- verified recovery/rollback reference;
- concurrency/version control where available;
- exact read-back acceptance;
- evidence persistence.

R2/R3/R4 are ineligible for component A2.

## Stage 11 — A2 CANARY
First component A2 promotion is a single narrow canary. Pre-change recovery point required. Post-write read-back mandatory. Any guardrail failure -> rollback/freeze. One rollback terminates the attempt.

## Stage 12 — A2 COMPONENT ENABLE
Only after canary PASS may routine deterministic R1 A2 be enabled for that component. Authority is revocable and component-scoped.

## Component-specific requirements

### DDS
Primary evidence must be solver-grounded, not model opinion. Preserve deal identity, DDS/Solver version/context, legal moves, equal-optimal moves and regret where available. Candidate must not redefine bridge correctness. Shared solver/cache changes are R2 unless proven isolated.

### Tournament analysis
Pin source tournament/session/board identity, auction/play evidence, DDS evidence and layout/template version. Separate analytical correctness from presentation. Changes to teaching interpretation/canon are R4.

### Lesson/material generation
Canonical School materials are authoritative. Technical formatting/export/QC changes may be R1; changes to bidding system, methodology or canonical explanations are R4. Regression must check factual fidelity to supplied canon and preservation of requested layout rules.

### Online school / Student Model
Start read-only. Identity mapping, permissions, student records and persistent profile writes are at least R3 until separately authorized. Student outcome claims require real longitudinal evidence, denominators and selection-bias controls. META may recommend pedagogical changes but cannot canonize them.

## Integration order
1. DDS
2. Tournament analysis
3. Lesson/material generation
4. Online school / Student Model

Order may change only for a documented dependency/reliability reason; convenience alone is not sufficient.

## School-wide governors
Every component remains under Cost Governor, Reliability Governor, Evidence provenance, typed Learning Memory, dependency impact and canonical boundary. A higher autonomy level in one component never propagates by inheritance.

## Completion criterion
META SCHOOL-WIDE INTEGRATION v1 is complete when all four component groups have at least A1 or a documented safety blocker, and each blocker is explicit rather than silently bypassed. A2 is optional per component and is not required for school-wide integration completion.