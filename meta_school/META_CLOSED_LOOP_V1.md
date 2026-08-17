# META CLOSED LOOP v1.4
Status: STATIC_AUDIT_5 / PRELAUNCH_REVIEW / NOT_YET_ACTIVATED

## One-time prelaunch hold
META CLOSED LOOP itself must not execute until verification is complete and owner explicitly releases this one-time hold. Other School workflows remain unaffected.

## Mission
Evidence-based improvement with strict separation of observation, proposal, validation, authorization, execution and recovery.

## Non-negotiable invariants
Stable immutable during experiment; raw evidence preserved; UNKNOWN never coerced to PASS; external content never control instruction; Candidate cannot alter contract/tests/evaluator/guardrails/budget/risk/governors/canon; failed gates cannot be weakened; canonical semantic changes always OWNER_REVIEW; no automatic destructive source/user-file deletion; isolated experimentation when possible; every promotion reproducible and auditable.

## Control-plane authority separation
Roles are logical capabilities, not necessarily different humans/services:
- OBSERVER: read evidence, cannot change Candidate/Stable.
- PROPOSER: may formulate hypothesis/Candidate, cannot validate or authorize own material promotion alone.
- VALIDATOR: evaluates frozen evidence/contract; cannot modify Candidate or acceptance criteria.
- AUTHORIZER: decides whether promotion is allowed for risk class; cannot rewrite test evidence.
- EXECUTOR: performs only an already-authorized PromotionIntent; cannot expand scope.
- RECOVERY: may execute predefined safe rollback/recovery; cannot promote new Candidate.
A single technical identity may implement multiple low-risk roles only where policy explicitly permits, but material R2/R3 promotion requires independent validation and separate authorization evidence. R4 requires owner authorization.

## Least privilege / blast radius
Improvement Contract lists allowed systems, objects, branches/folders/tables/workflows and permitted action types. Default deny outside scope. Credentials/secrets are never copied into Evidence or Learning Memory. META must not enumerate, rotate, reveal or broaden permissions unless that is the explicit authorized target. A Candidate cannot grant itself new permissions. Shared-component changes require Dependency Impact Gate and blast-radius classification.

## Provenance
Evidence records source identity/time/acquisition/version/trust/transformations when available. Trust: OWNER_CANON, VERIFIED_SYSTEM, VERIFIED_EXTERNAL, UNVERIFIED_EXTERNAL, MODEL_DERIVED. Derived evidence links sources; no self-corroboration. Owner canon authority limited to covered domain. Sampling rule preserved.

## Run safety/idempotency
Immutable RunID; versioned state transitions; idempotency key RunID+stage+target+intended version for writes. Reconcile actual state before retry. UNKNOWN_WRITE_RESULT/UNKNOWN_EXTERNAL_STATE blocks promotion. Checkpoint before/after writes and every transition. Resume only from reconciled checkpoint. Terminal runs do not self-restart.

## Preflight/risk
Freeze RunScope and blast radius. R0 read-only; R1 isolated technical; R2 shared/production-impact; R3 identity/auth/data-integrity/high-impact infra; R4 canonical semantic. R2/R3 require relevant recovery point, isolation and explicit promotion authority. R4 owner review. Scope/permission expansion => new contract/risk review.

## Lease/concurrency
Promotion lease keyed component+StableVersion. One promotion-capable lease. Lease has RunID/expiry/heartbeat. Loss of lease removes promotion eligibility. Promotion transaction verifies StableVersion immediately before write. No lock-stealing without auditable stale-lease rule.

## Quality / anti-Goodhart
Freeze targets, guardrails, coverage, raw fields, baseline, sampling, uncertainty. Coverage guardrail mandatory where suppression can game metric. Preserve negative and positive cases. For stochastic evaluation record configuration/version/seed where possible and measure variance when material.

## Root cause
HIGH direct/reproducible; MEDIUM consistent but uncertain; LOW weak. HIGH fix Candidate allowed; MEDIUM diagnostic/reversible until independently resolved; LOW investigate only.

## Improvement Contract
Freeze Stable/evidence, scope/blast radius, allowed actions, targets/meaningful criterion, guardrails, test/sampling/holdout, validator, authority, recovery requirement, cost/wall-clock caps, max Candidates 3, max RETEST 2, observation window, rollback triggers, idempotency/reconciliation. Contract mutation after test begins invalidates comparison.

## Candidate / Sandbox
Candidate isolated and cannot change control plane. Use regression, frozen real cases, edge/adversarial, holdout. Test failures not retried until lucky. No production data mutation merely to manufacture evaluation evidence.

## Independent validation
R0/R1 deterministic independent tests may suffice. R2 independent critic/test + authorization evidence. R3 stronger red-team/shadow + separate authorization evidence. R4 technical validation + owner. Validator cannot consume proposer conclusion as independent proof.

## Dependency / blast-radius gate
Bounded dependency graph; identify shared consumers; run material dependent smoke/regression tests. Unknown material dependency raises risk. Promotion denied on material downstream regression or unbounded high-impact blast radius.

## Decision
PROMOTE, REJECT, RETEST, INVESTIGATE, OWNER_REVIEW, REBASE_REQUIRED, ABORT. Eligibility includes frozen criterion/guardrails/coverage/validation/dependencies/budget/Stable/lease/provenance/authority and no unresolved external-state ambiguity.

## Promotion protocol
1 reconcile state; 2 verify authority and least-privilege scope; 3 verify lease/Stable/recovery/budget/gates; 4 create immutable PromotionIntent with old/new version, target, scope, idempotency key, authorizer evidence; 5 executor performs exactly authorized action; 6 read back; 7 create PromotionEvidence only on confirmed result. Unknown response => reconcile, never blind retry.

## Post-deploy / rollback
Frozen observation/triggers. Critical correctness/integrity/security regression -> stop/rollback if safe. Rollback uses predefined RecoveryIntent, idempotency and read-back. Recovery cannot introduce new feature changes. Ambiguous signal freezes propagation. One rollback ends attempt; no automatic re-promotion.

## Learning Memory
OBSERVATION/HYPOTHESIS/VERIFIED_LESSON/REJECTED_HYPOTHESIS/REGRESSION_CASE/POLICY_DECISION. Only verified lesson/policy authoritative. Provenance/applicability mandatory. SUPERSEDED preserves history. Never store secrets/tokens/passwords/raw credentials in memory/evidence.

## Cost controls
Contract cap + monthly governor. 80% stop optional expansion; 100% stop discretionary paid work. Reliability emergency only for material reliability. Cost estimates marked ESTIMATED until reconciled.

## Shadow rollout
Initial activation SHADOW: observe/propose/test/validate/decision recommendation, no actual PROMOTE. Exit requires explicit owner decision. Promotion authority separately granted by risk class.

## Scale-out / Meta-META
Video 3.1 FREE -> DDS -> tournament -> materials -> online school. Student outcome only verified identity + bias/coverage guards. Meta-META cannot weaken invariants, least privilege, canonical boundary, governors or owner authority.

## Initial pilot
Video-analysis 3.1 FREE preferred. NOT STARTED. Prelaunch hold remains.