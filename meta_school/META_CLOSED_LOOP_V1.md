# META CLOSED LOOP v1.1

Status: STATIC_AUDIT_2 / PRELAUNCH_REVIEW / NOT_YET_ACTIVATED

## One-time prelaunch hold

**Do not execute META CLOSED LOOP itself until its initial verification is completed and the owner explicitly releases this one-time hold.**

This restriction applies ONLY to META CLOSED LOOP during initial design/verification. It does not block other School algorithms, automations, monitors or normal workflows. Once approved, this hold is removed and a separate runtime trigger policy may be configured.

## Purpose

Controlled, evidence-based improvement of School algorithms while preserving canonical bridge/teaching content, production safety, recoverability, cost control and traceability.

## Core loop

WORK -> OBSERVE -> EVALUATE -> DETECT -> ROOT_CAUSE -> HYPOTHESIS -> IMPROVEMENT_CONTRACT -> CANDIDATE -> SANDBOX -> INDEPENDENT_VALIDATION -> DEPENDENCY_IMPACT -> COMPARE -> DECISION -> PROMOTE/REJECT/RETEST/OWNER_REVIEW -> POST_DEPLOYMENT_CONTROL -> LEARNING_MEMORY

## Global invariants

1. Stable is immutable during an experiment.
2. Raw evidence used for evaluation is preserved and cannot be rewritten to make Candidate look better.
3. Candidate cannot change its own success criteria, protected metrics, budget or gates after testing starts.
4. Canonical bidding system, teaching methodology and owner-approved course canon require OWNER_REVIEW for semantic changes.
5. No destructive source/user-file deletion is an automatic repair action.
6. No experiment runs directly on production when an isolated path exists.
7. A failed gate cannot be bypassed by lowering that gate in the same run.
8. Every PROMOTE must be reproducible from recorded Candidate + tests + evidence + Stable baseline.

## Stage 0 — Preflight and risk class

Identify StableVersion, scope, dependencies, rollback/recovery point, Cost Governor state and Reliability Governor state.

Assign risk class:
- R0: observation/read-only analysis;
- R1: isolated technical candidate, no production writes;
- R2: shared technical component or production-impacting change;
- R3: identity/authorization/data-integrity/high-impact infrastructure;
- R4: canonical teaching/bidding/methodology semantic change.

Reliability requirements are proportional:
- R0/R1 may proceed when required data is readable and relevant recovery evidence is at least INTEGRITY_VERIFIED where persistent data can be affected later;
- R2/R3 require a verified recovery point appropriate to the affected component plus isolated testing and explicit promotion gates;
- R4 always requires OWNER_REVIEW regardless of technical test result;
- full whole-school RECOVERY_READY is not a prerequisite for harmless observation/sandbox work, but missing relevant recovery capability blocks production-impacting promotion.

If the applicable preflight fails, STOP or OWNER_REVIEW. Do not weaken the requirement.

## Stage 1 — Inventory and dependency graph

Record AlgorithmID/component, StableVersion, inputs/outputs, direct and known downstream dependencies, metrics, cost/time signals, regression cases and relevant Evidence.

Acquire an experiment lease keyed by affected component/AlgorithmID + StableVersion. Only one promotion-capable experiment may hold the same component lease at a time. Parallel read-only analysis is allowed. Lease has owner/run ID, acquisition time and expiry/heartbeat. Stale leases may be released only by an auditable recovery rule.

## Stage 2 — Observer

Collect real evidence where available:
Input -> Run -> Result -> QC -> errors/corrections -> cost -> elapsed time -> Evidence.

Collect successful cases as well as failures. Missing evidence is UNKNOWN, not PASS/FAIL.

## Stage 3 — Quality model and anti-Goodhart controls

For the component define before Candidate testing:
- one or more primary target metrics;
- protected/guardrail metrics;
- raw evidence fields that cannot be discarded;
- baseline sample/corpus;
- measurement uncertainty/noise where known.

Do not treat lower detected-error count as improvement unless underlying raw cases show actual quality improvement. Coverage/completeness must be a guardrail whenever a metric can be improved by suppressing detections or outputs.

Canonical teaching materials supplied/approved by the owner outrank external or inferred methodology.

## Stage 4 — Opportunity detector

Classify only evidenced findings:
- ERROR;
- REGRESSION;
- INEFFICIENCY;
- IMPROVEMENT_OPPORTUNITY.

Finding != authorization to modify.

## Stage 5 — Root cause and confidence gate

Record what/where/cause/affected scope/recurrence/evidence and confidence:
- HIGH: direct evidence or reproducible causal mechanism;
- MEDIUM: multiple consistent signals but causal uncertainty remains;
- LOW: plausible hypothesis with weak/ambiguous evidence.

Rules:
- HIGH may create a fix Candidate;
- MEDIUM may create a diagnostic or reversible Candidate but cannot PROMOTE without independent validation resolving the material uncertainty;
- LOW -> INVESTIGATE/RETEST only; no promotion-capable Candidate.

## Stage 6 — Hypothesis

Create HypothesisID:
"If X changes to Y, target metric Z should improve by criterion C without violating guardrails A/B/C."

## Stage 6A — Improvement Contract

Freeze before Candidate evaluation:
- StableVersion and evidence snapshot;
- target metric(s);
- minimum meaningful improvement or explicit defect-removal criterion;
- guardrail metrics and maximum tolerated degradation;
- required test sets and minimum sample requirements where meaningful;
- evaluator/critic requirement;
- experiment cost cap;
- max wall-clock duration;
- max candidates per ProblemID: 3 by default;
- max RETEST cycles per HypothesisID: 2 by default;
- promotion authority required;
- post-deployment observation window and rollback thresholds.

If a numeric threshold is not scientifically justified, use an explicit binary acceptance condition plus independent review; do not invent fake precision.

Contract changes after testing begins invalidate the current comparison and require a new Hypothesis/Contract revision.

## Stage 7 — Candidate

Create isolated Candidate vN+1/revision/branch. Never overwrite Stable. Preserve rollback and evidence.

Before creating another Candidate for the same problem, enforce candidate/retest limits. Exceeding limits -> STOP/OWNER_REVIEW rather than an infinite search.

## Stage 8 — Sandbox

Use applicable layers:
1. regression corpus;
2. prior real cases not selected solely because Candidate performs well on them;
3. edge/adversarial cases;
4. holdout cases when enough data exists.

Candidate generation must not see holdout labels/results where practical.

## Stage 9 — Independent validation

The same reasoning path that proposed Candidate must not be the sole authority for material promotion.

Validation routing:
- R0/R1 low-impact deterministic fixes: deterministic independent tests may be sufficient;
- R2: require independent critic or independent test path (Cost Governor L2+ as appropriate);
- R3: require stronger independent verification/red-team/shadow path (L4 as appropriate);
- R4: technical validation + OWNER_REVIEW; META cannot autonomously make it canon.

Evaluator must consume preserved Stable/Candidate evidence under the frozen Improvement Contract.

## Stage 10 — Dependency Impact Gate

Before PROMOTE:
- re-read dependency graph;
- identify shared/downstream components;
- run available regression/smoke tests for materially affected dependents;
- if impact cannot be bounded, raise risk class or OWNER_REVIEW;
- no local improvement may be promoted when a material downstream regression is evidenced.

## Stage 11 — Stable vs Candidate comparison

Compare target + guardrails + coverage + reliability + cost + speed + regression risk where measurable.

PROMOTE eligibility requires all applicable conditions:
- target meets frozen acceptance criterion;
- no guardrail exceeds allowed degradation;
- required independent validation passes;
- dependency gate passes;
- experiment stayed within authorized cost/time envelope or received explicit escalation;
- StableVersion still matches the version named in the contract.

If Stable changed concurrently, comparison is stale -> REBASE/RETEST, never blind PROMOTE.

## Stage 12 — Decision gate

Decisions:
- PROMOTE;
- REJECT;
- RETEST;
- INVESTIGATE;
- OWNER_REVIEW;
- REBASE_REQUIRED.

Technical changes may be autonomously promoted only when the configured authority for that risk class permits it and all gates pass.

Canonical semantic changes to bidding system, teaching methodology, canonical course content or pedagogical rules always require OWNER_REVIEW and explicit owner approval.

## Stage 13 — Promotion transaction

Immediately before promotion:
1. verify experiment lease still valid;
2. verify StableVersion has not changed;
3. verify recovery/rollback point applicable to risk class;
4. verify budget and all gate evidence;
5. record PromotionEvidenceID;
6. promote atomically where platform permits;
7. preserve previous Stable and rollback reference.

If any check changed -> abort promotion and REBASE/RETEST.

## Stage 14 — Post-deployment control and rollback

Improvement Contract must define observation window and rollback triggers before promotion.

Default principle when component-specific thresholds are absent:
- any evidenced critical correctness/data-integrity/security regression -> immediate stop/rollback when a verified safe rollback path exists;
- guardrail breach beyond frozen tolerance -> rollback or OWNER_REVIEW;
- ambiguous signal -> freeze further propagation and investigate, not repeated automatic flip-flopping.

Use rollback hysteresis/cooldown: one rollback ends the promotion attempt. Do not auto-promote the same Candidate again without new evidence and a new decision record.

Add confirmed regressions to regression corpus.

## Stage 15 — Typed Learning Memory

Every memory item has a type and evidence state:
- OBSERVATION: raw/derived observation, not causal truth;
- HYPOTHESIS: proposed explanation, unverified;
- VERIFIED_LESSON: passed defined evidence/validation requirements;
- REJECTED_HYPOTHESIS: tested and not supported;
- REGRESSION_CASE: reproducible failure case;
- POLICY_DECISION: owner/governor rule.

Only VERIFIED_LESSON and POLICY_DECISION may be treated as authoritative reusable guidance. Hypotheses never silently become facts.

## Stage 16 — Cost controls during run

In addition to monthly Cost Governor limits, each Improvement Contract has an experiment cost cap and wall-clock cap.

At 80% of experiment cap: stop optional expansion and finish already-required validation if safe.
At 100%: stop discretionary paid work; return RETEST/OWNER_REVIEW unless reliability-emergency policy legitimately applies.
Reliability emergency budget cannot be used for routine META experimentation, cosmetic improvements or convenience.

Record actual cost per Candidate and per verified improvement where measurable.

## Stage 17 — Shadow Mode rollout

Initial activation uses SHADOW mode:
- META may observe, diagnose, generate Candidate, test, compare and produce a proposed decision;
- META MUST NOT actually PROMOTE during Shadow Mode;
- proposed PROMOTE is recorded as SHADOW_PROMOTE_RECOMMENDATION;
- owner/reviewer compares META decision with independent evidence;
- disagreements become calibration/regression cases.

Exit Shadow Mode only after an explicit owner decision based on successful pilot evidence. Promotion authority is granted separately by risk class; it is not implied by leaving prelaunch hold.

## Stage 18 — Scale-out order

After successful pilot/calibration:
1. video analysis 3.1 FREE;
2. DDS;
3. tournament analysis;
4. lesson/material generation;
5. online school and Student Model.

## Stage 19 — Student outcome layer

When evidence exists:
StudentID -> Lesson -> Decision/Error -> Feedback -> Homework -> Recheck -> Skill change.

Never invent student outcomes or use unverified identity mappings. Student outcome metrics must retain coverage/selection-bias guardrails.

## Stage 20 — Meta-META

Only after sufficient verified closed-loop evidence, evaluate which tests predict production, which metrics are useful, failed experiments, unnecessary checks and cost per verified improvement. Meta-META cannot silently weaken the invariants, canonical boundary, Reliability Governor or Cost Governor.

## Runtime state machine after activation

PREFLIGHT -> LEASE -> INVENTORY -> OBSERVE -> EVALUATE -> DETECT -> ROOT_CAUSE -> HYPOTHESIS -> CONTRACT -> CANDIDATE -> SANDBOX -> INDEPENDENT_VALIDATION -> DEPENDENCY_IMPACT -> COMPARE -> DECISION

Decision transitions:
- REJECT -> RECORD -> RELEASE_LEASE -> STOP
- INVESTIGATE -> RECORD -> RELEASE_LEASE -> STOP
- RETEST -> bounded SANDBOX/VALIDATION loop
- REBASE_REQUIRED -> release/reacquire against new Stable -> new comparison
- OWNER_REVIEW -> WAIT without promotion
- SHADOW_PROMOTE_RECOMMENDATION -> RECORD -> RELEASE_LEASE -> STOP
- PROMOTE -> PROMOTION_TRANSACTION -> POST_DEPLOYMENT_CONTROL -> RECORD -> RELEASE_LEASE -> STOP

Any cost/reliability/canonical-boundary failure -> STOP/OWNER_REVIEW as appropriate.

## Initial pilot

Preferred pilot: video-analysis algorithm 3.1 FREE.

**Current state: NOT STARTED. This document was hardened by static audit only. META CLOSED LOOP remains under the one-time prelaunch hold.**
