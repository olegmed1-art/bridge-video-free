# META SCHOOL OPERATIONS v1.0

Status: OPERATIONAL_POLICY

## Purpose
Operate the already integrated META School continuously without uncontrolled autonomy escalation.

## Current component authority
- Video analysis: A2 for bounded deterministic R1 only.
- DDS: A1.
- Tournament analysis: A1.
- Lesson/material generation: A1.
- Online school / Student Model: A1 READ-ONLY; student/profile/identity writes remain R3.

## Continuous cycle
For each component:
OBSERVE -> HEALTH_CHECK -> FINDING -> ROOT_CAUSE -> CANDIDATE_OR_NO_CHANGE -> SANDBOX -> REGRESSION -> DEPENDENCY_CHECK -> DECISION -> EVIDENCE -> LEARNING_MEMORY -> HEALTH_METRICS.

No finding is a valid outcome. META must not create work merely to keep the loop active.

## Scheduler priority
P0 reliability/data-integrity/security/canonical-boundary incident.
P1 confirmed regression affecting current School work.
P2 repeated quality defect with evidence.
P3 measurable efficiency/cost opportunity.
P4 speculative improvement.

P0/P1 preempt P2-P4. P4 cannot consume reliability emergency budget.

## Health metrics
Per component and globally record:
- observed_runs;
- findings;
- no_change_rate;
- candidate_count;
- validation_pass_rate;
- rejected_candidate_rate;
- rollback_count/rate;
- unknown_external_state_count;
- stale/conflicted_evidence_count;
- dependency_gate_failures;
- owner_review_count;
- cost_estimated/actual where available;
- time_to_verified_improvement;
- verified_improvements;
- cost_per_verified_improvement when cost exists.

Metrics are descriptive; thresholds are not invented. Alert thresholds require evidence or owner policy.

## Learning Memory promotion
OBSERVATION and HYPOTHESIS are non-authoritative.
A lesson becomes VERIFIED_LESSON only after reproducible validation and applicable-scope evidence.
Rejected hypotheses are retained to prevent repeated failed experiments.
Regression cases are added append-only with provenance.
R4 POLICY_DECISION remains owner-controlled.

## Component maturity progression
A1 -> A2 only after a real successful A1 improvement cycle, executable regression evidence, recovery/read-back proof and bounded deterministic R1 scope.
A2 does not imply A3/R2.
No authority inheritance across components.
Student Model writes do not become A2 through technical convenience; identity/profile persistence remains R3 until separately authorized.

## Terminology
In Russian School-facing material use «торговля», not «аукцион». Machine/source field names may remain unchanged when renaming would break compatibility; user-visible rendering must use «торговля».

## Operational completion
The system is healthy when all active components either have current evidence or an explicit UNKNOWN/BLOCKED state, no silent promotion bypass exists, and unresolved P0/P1 findings are visible rather than suppressed.