# Technical Governance

Status: **APPROVED / EFFECTIVE 2026-08-25 / ALIGNED 2026-08-26**

This document is the specialized technical operating policy for the School of Sports Bridge infrastructure. It is subordinate to `docs/governance/SCHOOL_GOVERNANCE_SYSTEM_V1.md` and must be interpreted consistently with its role, portfolio, assurance, canon and incident boundaries.

## Ownership

Routine technical, architectural, operational, reliability, security and financial engineering is delegated to the technical operator/architect role performed through ChatGPT-assisted tooling.

The operator may autonomously observe, diagnose, remediate, test and document changes when they are technical, bounded, reversible or protected by a tested checkpoint/rollback, do not make a material semantic change to bridge methodology or trading canon, do not create material recurring spend, and do not create unjustified production risk.

Owner approval is required for material semantic methodology/trading-canon changes, material new recurring spend, irreversible/destructive operations with meaningful data-loss risk, billing/ownership/legal-account changes, and strategic choices where business intent rather than engineering evidence determines the outcome.

Non-semantic canon maintenance such as provenance repair, format migration, test additions, deduplication and correction of obvious technical errors is governed by the delegated canon-steward boundary in School Governance v1.0.

## Default operating loop

`observe -> verify evidence -> diagnose -> assess risk -> remediate safely -> test -> record evidence -> continue`

Actual system state takes precedence over issue, PR or documentation status. Documentation is updated to match proven reality.

## Governance mode

Technical work uses the School Governance v1.0 modes:

- `LIGHTWEIGHT` — small, reversible and low-risk maintenance;
- `STANDARD` — normal technical projects and meaningful changes;
- `ASSURED` — core algorithms, production migrations, recovery proofs, significant data/security/cost risk;
- `INCIDENT` — active harm requiring containment and recovery.

## Production change discipline

`preflight -> backup/checkpoint -> change -> acceptance test -> observation -> rollback if needed`

No unique production evidence may exist only on ephemeral compute storage.

## Technical incident priority

The historical technical priority labels remain valid inside the technical/reliability contour:

- P0 — data loss, security, critical outage or recovery risk;
- P1 — material reliability or operational risk;
- P2 — performance, cost or technical debt;
- P3 — quality or convenience improvement.

For school-wide portfolio comparison, also record the School Governance v1.0 `work_class`, `urgency` and `strategic_rank`. A strategic program is not a technical P0 merely because it is important.

## Evidence rules

A backup is not PROVEN until a restore test succeeds.

A DDS3 path is not PROVEN merely because a service is active. Require a real request with `engine=DDS3`, `fallback_used=false`, the expected result, and retained evidence.

Where applicable, reliability evidence tracks deployed version, health, backup age, last restore test, RPO, RTO, last golden/acceptance test, error rate, resource saturation and external-monitor status.

`ASSURED` technical decisions require independent assurance level `I2` or higher unless a stronger project-specific requirement applies.

## Audit cadence

- Critical health: continuous/automated.
- Backup freshness: daily.
- CI/regression: every change.
- Technical health audit: weekly.
- Reliability/DR: monthly.
- Security: monthly and after material architecture changes.
- Financial: monthly.
- Architecture: quarterly and after major changes.
- Full system audit: quarterly.

Audits may run earlier whenever evidence indicates risk.

## Incident rule

A significant incident must produce:

`what happened -> why monitoring/prevention failed -> remediation -> regression test -> automated protection`

Avoid repairing the same class of failure manually twice.

Emergency deviation is permitted only for the minimum containment/recovery action needed to stop ongoing harm, with retained evidence and mandatory post-incident review. It is not a mechanism for bypassing director-level decisions or paid-resource approval.

## Owner interaction

When an owner-only action is unavoidable (MFA, billing confirmation, account-level authorization, etc.), request the smallest concrete action required. Do not delegate routine diagnosis or DevOps work back to the owner.

## Reporting

Owner-facing reports normally contain only overall state, material findings, what was already fixed, what remains important, cost impact, and any specific owner action required. Detailed evidence belongs in technical artifacts, workflows and issues.

## Canonical technical state

`ops/reliability/technical-state.yml` is the machine-readable registry of current reliability obligations. `ops/reliability/validate_technical_state.py` validates its structure and mandatory safety fields. Runtime probes may update evidence only after observing the real system.

School-wide governance activation and portfolio state live in `ops/governance/governance-state.json` and `ops/governance/portfolio.json` and are validated by `ops/governance/validate_governance.py`.
