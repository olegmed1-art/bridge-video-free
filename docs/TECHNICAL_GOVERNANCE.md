# Technical Governance

Status: **APPROVED / EFFECTIVE 2026-08-25**

This document is the canonical technical operating policy for the School of Sports Bridge infrastructure.

## Ownership

Routine technical, architectural, operational, reliability, security and financial engineering is delegated to the technical operator/architect role performed through ChatGPT-assisted tooling.

The operator may autonomously observe, diagnose, remediate, test and document changes when they are technical, bounded, reversible or protected by a tested checkpoint/rollback, do not change bridge methodology/trading canon, do not create material recurring spend, and do not create unjustified production risk.

Owner approval is required for methodology/trading-canon changes, material new recurring spend, irreversible/destructive operations with meaningful data-loss risk, billing/ownership/legal-account changes, and strategic choices where business intent rather than engineering evidence determines the outcome.

## Default operating loop

`observe -> verify evidence -> diagnose -> assess risk -> remediate safely -> test -> record evidence -> continue`

Actual system state takes precedence over issue, PR or documentation status. Documentation is updated to match proven reality.

## Production change discipline

`preflight -> backup/checkpoint -> change -> acceptance test -> observation -> rollback if needed`

No unique production evidence may exist only on ephemeral compute storage.

## Priority model

- P0 — data loss, security, critical outage or recovery risk.
- P1 — material reliability or operational risk.
- P2 — performance, cost or technical debt.
- P3 — quality or convenience improvement.

## Evidence rules

A backup is not PROVEN until a restore test succeeds.

A DDS3 path is not PROVEN merely because a service is active. Require a real request with `engine=DDS3`, `fallback_used=false`, the expected result, and retained evidence.

Where applicable, reliability evidence tracks deployed version, health, backup age, last restore test, RPO, RTO, last golden/acceptance test, error rate, resource saturation and external-monitor status.

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

## Owner interaction

When an owner-only action is unavoidable (MFA, billing confirmation, account-level authorization, etc.), request the smallest concrete action required. Do not delegate routine diagnosis or DevOps work back to the owner.

## Reporting

Owner-facing reports normally contain only overall state, material findings, what was already fixed, what remains important, and any specific owner action required. Detailed evidence belongs in technical artifacts, workflows and issues.

## Canonical technical state

`ops/reliability/technical-state.yml` is the machine-readable registry of current reliability obligations. `ops/reliability/validate_technical_state.py` validates its structure and mandatory safety fields. Runtime probes may update evidence only after observing the real system.
