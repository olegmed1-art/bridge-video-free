# META School

Status: DESIGNED

META School is the governing self-improvement layer for the Sports Bridge School. It extends existing production processes; it does not replace them.

## Constitution

1. Teacher-approved bidding and teaching methodology are protected and may not be changed autonomously.
2. Hypotheses are never promoted to teacher rules automatically.
3. UNKNOWN is preferable to invented certainty.
4. Material knowledge must preserve provenance.
5. A new version never destroys the last Stable version.
6. A material corrected failure creates or updates a regression test.
7. Failed experiments are retained as negative knowledge.
8. No change is called an improvement without independent evidence.
9. Changes to shared components require dependent regression checks.
10. At equal quality, prefer the simpler and cheaper solution.
11. Repetition of a known error is a META failure and increases priority.
12. Protected methodology changes require teacher approval.

## Status model

DESIGNED -> IMPLEMENTED -> TESTED -> OPERATIONAL

Promotion is evidence-gated. Existence of code or a local successful run is not enough for OPERATIONAL.

Minimum evidence record:
- EvidenceID
- component_id and version
- source identity
- RunID and/or immutable Artifact/FileID
- test/regression result
- timestamp
- provenance

OPERATIONAL requires reproducible end-to-end evidence and a repeated run/regression check appropriate to the component.

## Core layers

1. Constitution + Human Authority
2. Governor
3. Identity & Provenance
4. Registry / Knowledge / Artifact Manifest
5. Orchestrator over existing production algorithms
6. Adaptive Quality Engine (L0 code checks -> L1 semantic review -> L2 independent critic -> L3 candidates -> L4 red-team/shadow -> L5 teacher review)
7. Evidence Gate
8. Failure Intelligence + Root Cause
9. Experiment Lab: Stable / Lab / Candidate / Promote / Rollback
10. Historian + Metrics
11. Proactive Improvement
12. Discovery / Watcher
13. Architect + Complexity Auditor / SIMPLIFY

## Identity rules

Names and folder names are not identity. Use stable PersonID/StudentID plus SourceIdentity and external identity mappings. Student-learning joins are blocked until identity is resolved.

## Data rules

Neon remains the system of record. META must extend the existing schema through controlled migrations; it must not create a competing source of truth. New migrations are never treated as production merely because they exist in the repository.

## Artifact rules

Every material output should be traceable to source data, algorithm/version, RunID, Artifact/FileID and checksum where practical. Access-control changes are protected operations and are not autonomous self-improvement actions.

## Improvement loop

Production -> Quality -> Evidence Gate -> Failure/Root Cause when needed -> Lab candidates -> Regression/Golden/Red-Team according to risk -> Promote or Rollback -> Historian/Metrics -> Proactive Improvement.

## First-stage KPI

The first objective is not maximizing the number of experiments. It is increasing the number of existing school components with reproducible evidence of their actual status and operation.

Primary metrics:
- Repeat Error Rate
- First Pass Acceptance
- Teacher Intervention Rate
- Regression Rate
- Detection Before Delivery
- Learning Velocity
- Improvement per cost

## Build order

1. Truth Layer: Registry + Identity + Provenance + RunID + ArtifactManifest
2. Evidence Layer: common status/evidence gates
3. Quality Layer: regression + Golden Set + Failure Base + dependency graph
4. Governor: adaptive checks + retry/checkpoint/rollback
5. Learning: failures -> hypotheses -> candidates -> experiments -> evidence
6. Evolution: proactive weakness discovery
7. Student Learning after reliable identity
8. Discovery/Watcher/Red Team and META self-audit

## Budget policy

Prefer deterministic code over AI whenever code can verify the requirement reliably. Escalate AI depth by uncertainty, impact and risk. Budget is a ceiling, not a spending target.