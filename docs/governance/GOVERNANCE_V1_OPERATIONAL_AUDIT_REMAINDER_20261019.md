# Governance v1 operational audit: exact remaining criteria

As of 2026-10-19. Tracker: [#620](https://github.com/olegmed1-art/bridge-video-free/issues/620).
Status: **OPEN / OVERDUE / PARTIAL EVIDENCE**. Original due date: 2026-09-25.
Scope of this update: public GitHub evidence and existing committed registries.
It is not a completed school-wide operational audit or a change of authority.
Audit evidence window for this remainder: activation on **2026-08-26** through
**2026-10-19T17:45:02Z**. Any later extension must state its new cutoff explicitly.

The evidence baseline was main `1742ba4ed046e6d8c661341916b982465187087a`.
The current governance-state declares v1 ACTIVE/CANONICAL. Issues #611, #609,
#782, #620 and #627 remain open in the checked primary sources. The bounded
read does not establish current runtime, all incidents, actual costs, or student outcomes.
"Not evidenced" below means absent from this bounded evidence set, not proof
that the activity or incident never occurred.

## Required evidence: remaining acceptance work

| # | Criterion from #620 | Evidence available | Status and exact remaining work |
| --- | --- | --- | --- |
| 1 | Current governance-state and portfolio | Canonical state; this partial portfolio reconciliation; dated source links | PARTIAL. Reconcile remaining services, capability acceptance and missing portfolio entries. The prior scoped registry baseline date remains 2026-08-30; it never certified a complete inventory. |
| 2 | Material work classified by governance mode | Existing three value entries are ASSURED; current Observer correction is ASSURED | PARTIAL. Enumerate material work within the stated audit window, with one mode and rationale per item; reconcile historical FULL_MODEL labels to current modes. |
| 3 | LIGHTWEIGHT, STANDARD/ASSURED and actual INCIDENT usage | [STANDARD read-only preflight](../ops/AUTOPILOT_ORACLE_DB_PREFLIGHT_20260922.md); [ASSURED Observer evidence](../evidence/global_school_observer_freshness_2026-10-19.md) | PARTIAL. Retain a concrete LIGHTWEIGHT example. Identify actual INCIDENT cases from the authorized operational record, or document a bounded search and explain that absence is not proven. |
| 4 | Director technical-escalation count and examples | No school-wide event ledger or denominator established here | OPEN. Define the eligible population, count technical versus owner-only escalations, retain redacted examples, and compare with a baseline. Do not use chat count as a proxy. |
| 5 | Governance overhead by mode | CI/review examples exist, but no mode-level time/cost sample | OPEN. Measure elapsed and active review work per mode on a bounded sample, include uncertainty and baseline. Workflow duration alone is not total governance overhead. |
| 6 | Duplicate/stale project-state findings | [Old shadow run](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39241231676); [corrected shadow](https://github.com/olegmed1-art/bridge-video-free/actions/runs/39295595183); two old verification dates preserved | PARTIAL. Stale-index defect and bounded remediation are evidenced. Reconcile remaining duplicate ownership/assignments; do not close historical dispatch PRs in bulk. |
| 7 | Red Team findings and false-PASS prevention | PR #2185 I2 review found truncated-response failure; repair and independent re-review passed; 27 Observer tests / 37 full CI tests | PARTIAL. This is one actual ASSURED example, not coverage of all material work. Sample the remaining ASSURED decisions and record assurance level and unresolved findings. |
| 8 | Observatory evidence quality | Current Observer emits CONFIRMED/STALE/BLOCKED/COMPLETED/UNKNOWN; four findings/previews and stable keys in canary/shadow | PARTIAL. Record positive-signal adjudication and dedupe scope; identify unobserved systems. External delivery and persistent cross-run suppression remain unproven. Follow the [notification acceptance plan](../operations/GLOBAL_SCHOOL_OBSERVER_NOTIFICATION_ACCEPTANCE.md). |
| 9 | School Canon / World Knowledge boundary incidents | #609 and existing project-state define the boundary | OPEN. Check actual activation/provenance and incident evidence for the audit period. A policy document or clean code test is not evidence of zero boundary incidents. |
| 10 | Service ownership and recovery gaps | Historical Research Lab/Canon descriptions retained; Observer service added with bounded scope and rollback | PARTIAL. Verify current service-to-owner mapping, recovery paths, retained backup/restore evidence and dates for each material service. Do not infer recovery from source rollback alone. |
| 11 | Privacy/data/FinOps findings | Historical data classes and cost boundaries exist; no current complete audit performed here | OPEN. Review data handling/publication, actual usage/billing and commitments using authorized sources. Keep sensitive operational details in approved private evidence; publish only permitted summaries. No new spend or access changes are authorized. |
| 12 | Unresolved inventory gaps | Explicit `inventory_gaps` and `unverified_scopes` in the reconciled indexes | PARTIAL. Assign an existing owner and primary evidence target to every gap; retain unknowns until verified. Close only gaps backed by the required evidence. |

## Answers still required for the eight audit questions

1. **Forgotten, duplicated or falsely completed work:** one false-clean Observer
   defect is proved and repaired. A reduction across school work is not measured;
   complete criteria 2, 4 and 6 before claiming benefit.
2. **Routine Director questions:** unresolved; criterion 4 needs a baseline and denominator.
3. **Useful role separation versus duplication:** one independent review caught a
   real defect; school-wide efficiency still needs criteria 5 and 7.
4. **I2 for ASSURED work:** shown for #2185 only in this sample; inventory and sample
   other material decisions under criteria 2 and 7.
5. **LIGHTWEIGHT stayed lightweight:** unresolved; retain an actual example and measured overhead.
6. **Incident containment delay:** unresolved until actual incident cases and timing
   are checked; do not equate no incident record here with no incidents.
7. **Primary-source/project-state reconciliation:** corrected for the bounded
   GitHub facts; runtime, complete portfolio and benefit metrics remain unverified.
8. **Rules to simplify/strengthen/remove:** the index freshness correction is
   routine maintenance. A governance-wide proposal still needs evidence and tradeoffs.

## Closure gate and next owner work

- Existing AI governance owner: assemble the missing work inventory, mode samples,
  escalation/overhead evidence and service recovery matrix. Use subsystem owners
  for current facts; do not operate their services as part of this audit.
- Record one justified outcome: KEEP, ADJUST, SIMPLIFY, PARTIALLY_RETIRE or REPLACE.
  **No outcome is selected by this partial update.**
- Distinguish unresolved risk from accepted risk and give each outstanding item an owner.
- Any fundamental authority change still requires Director approval. This document
  changes no policy, permission, safety gate, service state or spending limit.
- Keep #620 open until the evidence and outcome are complete. The original due
  date is retained so that the overdue state remains visible.

Rollback of this bookkeeping update is a source revert. Historical snapshots are
retained in Git; no migration or runtime action is involved.

## Validation of this update only

Governance registry validation, the existing project-state self-test and all 27
Observer tests passed. An invariant check confirmed that historical deployment,
verification and next actions, budgets, safety invariants and prior verification
dates were preserved. Replaying the edited indexes still produces four findings
and four local previews with delivery NOT_ATTEMPTED; a partial edit does not erase
the stale-coverage warnings. Independent I2 review found no blocking issue in this
bookkeeping/plan scope. These checks do not close the operational criteria above.
