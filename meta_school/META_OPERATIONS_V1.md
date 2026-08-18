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

## Adaptive process observation

Observation depth is selected before a run and recorded in its frozen contract. The objective is to learn from real execution without spending full diagnostic resources on every stable repetition.

### Observation profiles
- **FULL** — for a new component or algorithm, the first run after a material change, migrations/recovery drills, unstable or previously failed flows, expensive/long-running jobs and any R2-R4 work. Capture stage timing, retries, errors, resource/cost signals, outputs, guardrails and relevant logs.
- **SELECTIVE** — for established stable flows. Capture identity/version, start/end/result, key health and quality metrics, cost where available, warnings/errors and final verification.
- **TRIGGERED_FULL** — temporarily escalates a SELECTIVE run to full diagnostics when a frozen trigger fires: error/retry, guardrail or quality regression, unusual duration/resource use, changed dependency, incomplete evidence or UNKNOWN/STALE/CONFLICTED state.

### Baseline non-interference
During a valid baseline observation, OBSERVER is read-only and does not tune, retry, repair or otherwise alter the running process. Intervention is allowed only to prevent credible data loss, security/integrity harm, irreversible damage, budget-cap breach, or when the process cannot physically continue. Every intervention is recorded and the run is marked **INTERVENED**; its measurements cannot be treated as an untouched baseline.

### Adaptive resource rule
- Start FULL when required by profile criteria; otherwise use SELECTIVE.
- Reduce FULL to SELECTIVE only after repeated comparable successful runs show stable results and no unresolved P0/P1/P2 finding. The required run count or observation window must be frozen from evidence or owner policy; META must not invent a convenient threshold after seeing results.
- Return immediately to TRIGGERED_FULL after a material change, anomaly, regression, incident, dependency drift or evidence gap.
- Deep re-analysis is performed only when a trigger fires or enough new evidence exists to test a defined hypothesis.
- Observation has explicit cost and wall-clock caps. Optional diagnostics stop when their expected information value no longer justifies their resource cost; mandatory safety/evidence gates remain fail-closed.

### Post-run learning
After every observed run, record either a finding or **NO_CHANGE**, plus observation profile, interventions, evidence completeness, resource use and conclusion. Analyze result and process separately. Root cause and improvement proposals are created only after the observed run ends, except for permitted safety intervention. Proposed changes use SANDBOX and REGRESSION gates; only verified lessons enter authoritative Learning Memory.

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