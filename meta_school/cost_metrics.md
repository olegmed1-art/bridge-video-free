# META School cost/value metrics

Status: DESIGNED

Cost accounting must be based on recorded Cost Events, not estimates once production telemetry exists.

## Core metrics

- TotalCostMonthly = sum(amount_usd)
- CostPerActiveStudent = TotalCostMonthly / active_students
- AICostPerLesson = AI-attributed cost / processed lessons
- AICostPerTournament = AI-attributed cost / processed tournaments
- AICostPerVideoHour = video+AI attributed cost / processed video hours
- CostPerExperiment = experiment-attributed cost / completed experiments
- CostPerPreventedError = improvement/quality cost / verified errors detected before delivery
- CostPerVerifiedImprovement = experiment/improvement cost / promoted improvements with evidence
- CacheReuseRate = reusable-result hits / eligible reuse requests
- DeterministicCheckShare = L0 checks / all quality checks
- TeacherInterventionCostProxy = teacher review events and estimated review time; monetary conversion is optional and must be explicitly configured
- MarginalImprovementPerDollar = change in selected verified quality metric / incremental cost

## Guardrails

Cost metrics must never reward hiding errors, suppressing UNKNOWN, reducing necessary teacher escalation, weakening evidence thresholds, or lowering educational quality merely to reduce spend.

A cost reduction is accepted only when regression/evidence shows protected quality is preserved.

## Baseline process

1. Record all attributable paid operations as Cost Events.
2. Link Cost Events to RunID and component where possible.
3. Link experiments to their total costs.
4. Link promoted improvements to evidence and before/after metrics.
5. After sufficient production data, replace planning assumptions with rolling 30/90-day observed values.
6. Re-estimate soft/hard budget limits only from observed demand and marginal value.
