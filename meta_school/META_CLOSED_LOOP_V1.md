# META CLOSED LOOP v1.0

Status: DEFINED / VALIDATED-AS-SPEC / DISABLED

## Hard start rule

**DO NOT RUN THIS ALGORITHM WITHOUT AN EXPLICIT OWNER COMMAND.**

The algorithm is manual-start only. No schedule, webhook, workflow, observer, child process, agent, automation, or other algorithm may initiate a META CLOSED LOOP run implicitly.

Accepted start condition: a direct owner instruction clearly ordering META CLOSED LOOP to start/run for a named scope. Ambiguous requests, normal school work, health checks, monitoring, and background automation are not start authorization.

After a run finishes, the algorithm returns to DISABLED/WAITING_FOR_OWNER_COMMAND. It does not self-repeat.

## Purpose

Create a controlled evidence-based improvement loop over School algorithms while preserving the owner's bridge system, teaching methodology, canonical materials, production data, reliability and budget constraints.

## Core loop

WORK -> OBSERVE -> EVALUATE -> DETECT -> ROOT_CAUSE -> HYPOTHESIS -> CANDIDATE -> SANDBOX -> A/B -> EVIDENCE -> DECISION -> PROMOTE/REJECT/RETEST/OWNER_REVIEW -> POST_DEPLOYMENT_CONTROL -> LEARNING_MEMORY

## Stage 0 — Reliability and cost preflight

Before any candidate change:
- identify current Stable version;
- verify rollback/recovery point appropriate to the change;
- ensure production is not used as an experiment sandbox;
- check Cost Governor;
- check Reliability Governor;
- preserve sources and prior results;
- do not delete source/user files automatically.

If preflight fails, STOP the candidate path and record the blocker. Do not weaken a safety gate to make the run pass.

## Stage 1 — Inventory

For the run scope identify:
- AlgorithmID/component;
- Stable version;
- inputs and outputs;
- dependencies;
- available quality metrics;
- cost/time signals;
- existing regression cases;
- relevant Evidence.

## Stage 2 — Observer

Collect evidence from real executions where available:
Input -> Run -> Result -> QC -> errors/corrections -> cost -> elapsed time -> Evidence.

Collect successful cases as well as failures. Do not infer missing evidence as success or failure.

## Stage 3 — Quality model

Evaluate only metrics supported for the component. Shared dimensions may include:
- correctness;
- completeness;
- reliability;
- pedagogical value where a valid school reference exists;
- cost;
- speed;
- regression risk.

Canonical teaching materials supplied/approved by the owner outrank external or inferred methodology.

## Stage 4 — Opportunity detector

Classify findings as:
- ERROR — evidenced error;
- REGRESSION — worse than Stable/reference;
- INEFFICIENCY — correct but unnecessarily expensive/slow;
- IMPROVEMENT_OPPORTUNITY — evidence supports a potentially better approach.

A finding alone does not authorize a change.

## Stage 5 — Root cause

Before proposing a fix record:
- what happened;
- where;
- probable cause and confidence;
- affected scope;
- recurrence/history;
- evidence supporting the diagnosis.

Do not repair a guessed cause as if it were proven.

## Stage 6 — Improvement hypothesis

Create HypothesisID and state:
"If X is changed to Y, metric Z should improve by an observable criterion without unacceptable degradation of protected metrics A/B/C."

The success criterion must be testable before promotion.

## Stage 7 — Candidate

Never overwrite Stable for experimentation.
Create Candidate vN+1 or equivalent isolated revision/branch. Preserve Stable and recovery evidence.

## Stage 8 — Sandbox

Test Candidate outside production against, where applicable:
1. regression corpus;
2. prior real cases;
3. edge/adversarial cases.

Use component-specific tests for video analysis, DDS, tournament analysis, lesson generation and online-school components.

## Stage 9 — Stable vs Candidate comparison

Compare on the declared target metric plus protected metrics, including quality, regressions, cost, speed and reliability when measurable.

Candidate is not promoted merely because it runs. Evidence must show it is not materially worse on protected metrics and improves the declared target or fixes the evidenced defect.

## Stage 10 — Decision gate

Allowed decisions:
- PROMOTE;
- REJECT;
- RETEST;
- OWNER_REVIEW.

### Autonomous technical-change boundary

META may autonomously prepare/test technical improvements such as bug fixes, QC, deduplication, reliability, idempotency, performance and cost optimizations when they do not change canonical bridge/teaching content.

### Owner-controlled canonical boundary

META MUST NOT autonomously change or promote changes to:
- the owner's bidding system;
- teaching methodology;
- canonical course content;
- pedagogical rules presented as School canon;
- other owner-controlled canonical bridge decisions.

Such changes require OWNER_REVIEW and explicit owner approval before becoming canonical/Stable.

## Stage 11 — Evidence chain

Maintain traceability where supported:
RunID -> ProblemID -> HypothesisID -> CandidateID -> TestID -> Metrics -> Decision -> EvidenceID -> StableVersion.

Never label an improvement proven without supporting Evidence.

## Stage 12 — Promotion

PROMOTE only after required gates pass. Preserve previous Stable, rollback point, test evidence and reason for promotion. Never delete useful prior evidence as part of promotion.

## Stage 13 — Post-deployment control

Observe subsequent real runs. If a promoted technical change causes a material regression:
- stop propagation;
- rollback when an authorized safe automatic rollback path exists;
- preserve diagnostic evidence;
- add a regression case.

Canonical content is never silently altered during rollback/repair.

## Stage 14 — Learning memory

Record successful and failed hypotheses, applicability conditions, regression cases and evidence so failed experiments are not blindly repeated.

## Stage 15 — Scale-out order

Recommended rollout after a successful pilot:
1. video analysis 3.1 FREE;
2. DDS;
3. tournament analysis;
4. lesson/material generation;
5. online school and Student Model.

This order is a technical rollout recommendation, not authorization to start any run.

## Stage 16 — Student outcome layer

When online-school evidence exists, support:
StudentID -> Lesson -> Decision/Error -> Feedback -> Homework -> Recheck -> Skill change.

Do not invent student outcomes or use unverified identity mappings.

## Stage 17 — Meta-META

Only after sufficient closed-loop evidence exists, evaluate the improvement process itself: predictive value of tests, useful metrics, failed experiments, unnecessary checks, and cost per evidenced improvement.

Meta-META is also manual-start unless separately and explicitly authorized by the owner.

## State machine

DISABLED/WAITING_FOR_OWNER_COMMAND
  -> (explicit owner START command)
PREFLIGHT
  -> INVENTORY
  -> OBSERVE
  -> EVALUATE
  -> DETECT
  -> ROOT_CAUSE
  -> HYPOTHESIS
  -> CANDIDATE
  -> SANDBOX
  -> COMPARE
  -> DECISION
     -> REJECT -> RECORD -> STOP
     -> RETEST -> SANDBOX (bounded; no infinite loop)
     -> OWNER_REVIEW -> WAIT_FOR_OWNER
     -> PROMOTE -> POST_DEPLOYMENT_CONTROL -> RECORD -> STOP
  -> DISABLED/WAITING_FOR_OWNER_COMMAND

Any reliability/cost/canonical-boundary failure -> STOP/OWNER_REVIEW as appropriate.

## Anti-loop controls

- No self-start.
- No automatic recurring run.
- No recursive spawning of META CLOSED LOOP.
- RETEST must be bounded and justified by new evidence/change.
- A failed gate cannot be bypassed by lowering the gate during the same run.
- Do not promote based solely on META's own qualitative assertion.
- Do not modify production merely to create evidence for a candidate.

## Initial pilot

Preferred first pilot: video-analysis algorithm 3.1 FREE because it already has versioned revisions, production executions, QC, regression cases, GitHub/Drive/Neon evidence and rollback practice.

**Pilot remains NOT STARTED until explicit owner command.**
