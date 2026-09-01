# Autopilot bounded task ingress v1

Status: SHADOW DESIGN GATE. Tracks #1015 and #1013.

## Purpose
Provide the missing safe path from an approved Director request to canonical Autopilot task registration without enabling arbitrary task creation or production routing.

## Initial allow-list
The first real task kind is `IBF_READ_ONLY_ANALYSIS` only. Input is an IBF player number. The task may perform bounded reads of official Israel Bridge Federation result pages and retain evidence. It may not mutate production systems.

## Required ingress checks
1. Authenticated caller identity and Director approval reference.
2. Explicit allow-listed task kind.
3. Schema validation and bounded input size.
4. Deterministic idempotency key; duplicate registration returns the existing task identity.
5. SHADOW_ONLY routing on the temporary Autopilot Neon branch.
6. Budget and retry limits before READY.
7. Append-only registration evidence.
8. Fail closed for unknown task kind, missing approval, missing capability, invalid input, or unavailable canonical state.

## Hard prohibitions
No arbitrary shell, arbitrary URL, production Neon mutation/routing, main write, merge, video, training, DDS3, BEN, or School Canon mutation. No model may invent a capability or bypass an allow-list.

## State transition
Validated ingress may create only `NEW`, then the existing state machine performs `VALIDATING -> READY -> RUNNING`. The ingress cannot directly create RUNNING/DONE tasks or alter terminal evidence.

## Acceptance test
Issue #1013 is the first end-to-end acceptance test:
`15031 -> register -> task-id -> Oracle CLAIM -> official IBF discovery -> personal boards/field comparison -> truthful terminal evidence`.
No manual IBF URL from the Director. Missing source data must be recorded, never invented.

## Promotion rule
Passing this ingress acceptance test permits continued Limited read-only pilot only. Full Production remains blocked until every promotion blocker in `ops/autopilot/project-state.json` is independently closed and a separate evidence-backed Director GO is recorded.
