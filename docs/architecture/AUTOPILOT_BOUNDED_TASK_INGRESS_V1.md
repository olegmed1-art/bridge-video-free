# Autopilot bounded task ingress v1

Status: SHADOW IMPLEMENTATION GATE. Tracks #1015 and #1013.

## Purpose
Provide the missing safe path from an approved Director request to canonical Autopilot task registration without enabling arbitrary task creation or production routing.

## Initial allow-list
The first real task kind is `IBF_READ_ONLY_ANALYSIS` only. Input is an IBF player number. The task may perform bounded reads of official Israel Bridge Federation result pages and retain evidence. It may not mutate production systems.

The first database RPC is deliberately narrower than a generic task creator: `autopilot.register_approved_ibf_analysis(...)`. The caller cannot provide an arbitrary task kind, capability, URL, source, cost budget or executor.

## Required ingress checks
1. Caller must use the existing bounded Autopilot runtime principal; direct table writes remain unavailable.
2. A Director approval reference is mandatory and syntax-bounded.
3. The IBF player identifier and task key are schema-bounded.
4. Task registration is deterministic and idempotent; exact replay returns the existing task identity while conflicting reuse fails closed.
5. The RPC fixes routing to the SHADOW_ONLY Autopilot state and fixes cost to zero.
6. At most three non-terminal IBF tasks and twelve new IBF tasks per hour may exist through this ingress; registration is serialized so concurrency cannot bypass the limits.
7. Every successful create records append-only `TASK_READY` evidence with the approval reference.
8. Unknown or invalid input, missing approval, a conflicting idempotency key, or a capacity violation fails closed.

## Hard prohibitions
No arbitrary shell, arbitrary URL, production Neon mutation/routing, main write, merge, video, training, DDS3, BEN, or School Canon mutation. No model may invent a capability or bypass an allow-list.

## State transition
The dedicated ingress RPC validates the complete narrow contract synchronously and then creates the canonical task directly in `READY`, matching the existing `create_shadow_task` contract. It records one `NEW -> READY` event. It cannot create `RUNNING` or terminal tasks, alter retained evidence, or bypass the existing lease/fencing state machine.

## Implementation boundary
Migration `0306_autopilot_bounded_task_ingress.sql` extends only the allow-listed goal/capability/evidence enums and exposes the dedicated registration RPC to `autopilot_runtime`. It does not grant generic `create_shadow_task`, direct table access, production routing, or external-event ingress. Worker support for `IBF_READ_ONLY_ANALYSIS` must be proven before this migration is activated on the temporary runtime branch.

## Acceptance test
Issue #1013 is the first end-to-end acceptance test:
`15031 -> register -> task-id -> Oracle CLAIM -> official IBF discovery -> personal boards/field comparison -> truthful terminal evidence`.
No manual IBF URL from the Director. Missing source data must be recorded, never invented.

## Promotion rule
Passing this ingress acceptance test permits continued Limited read-only pilot only. Full Production remains blocked until every promotion blocker in `ops/autopilot/project-state.json` is independently closed and a separate evidence-backed Director GO is recorded.
