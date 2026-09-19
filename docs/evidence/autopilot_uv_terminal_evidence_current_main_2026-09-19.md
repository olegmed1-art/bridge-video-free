# Universal Video terminal-evidence current-main repair

Date: 2026-09-19  
Governance: ASSURED  
Status: AUTOPILOT_WORKSPACE

## Purpose

Replace the stale and conflicting PR #1059 implementation with one bounded repair based on exact current main `4309253c17fa307706de2d0a19c79b3accf4c829`.

## Required safety properties

- Revalidate immutable source identity at the terminal boundary.
- Re-read and validate processor artifacts rather than trusting cached or caller-constructed mappings.
- Enforce terminal evidence inside the database transition so alternate callers cannot bypass it.
- Preserve fail-closed behavior for malformed, partial, stale, oversized, or mismatched evidence.
- Do not change Canon semantics, enable Canon/WORLD writes, run production media, deploy, or mutate production.

## Acceptance

- Minimal current-main implementation and migration with a guarded rollback.
- Focused SQL and Python contract tests.
- Existing Universal Video and migration checks remain green.
- Independent I2 evidence before any production promotion.
- Exact-head audit result recorded in this PR.

## Rollback

Revert this PR and use its guarded migration rollback before any later production application. No production application is authorized by this workspace commit.
