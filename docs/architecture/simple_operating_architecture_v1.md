# Simple operating architecture v1

Status: active top-level model.
Scope: whole Bridge School technical system.
Date: 2026-08-26.

This document is the simple operating map for day-to-day architectural decisions.
Detailed subsystem documents remain valid only inside these boundaries.

## One-sentence model

Codex coordinates, GitHub controls, Oracle computes, Neon and Drive store, Vercel shows.

## Five blocks

| Block | Role | Must not become |
| --- | --- | --- |
| Codex / ChatGPT | Translates director goals into bounded technical work, checks state, records evidence, asks for owner approval when needed. | A silent authority for spending, irreversible deletion, production promotion, or bridge-method changes without evidence. |
| GitHub | Holds code, pull requests, issues, workflows, release policy, and small durable evidence. | A permanent archive for large media outputs or a substitute for current external service state. |
| Oracle or replacement compute | Runs heavy work: DDS3, BEN, video workers, Assistant Lab jobs, batch analysis, and long-running processing. | A canonical data store or uncontrolled always-on cost center. |
| Neon + Drive | Store results. Neon stores structured truth: jobs, statuses, provenance, checksums, links. Drive stores large durable files: video, audio, reports, exports, recovery artifacts. | A mixed pile where large binaries go into Neon or operational state is hidden only in Drive folders. |
| Vercel | Serves the thin public web/API layer and only the web/API deployment path. | A worker platform for DDS3, BEN, video, batch processing, or routine CI for unrelated changes. |

## Default flow

1. Director states the goal.
2. Codex reconciles current state from primary sources.
3. GitHub records the work path: issue, branch, PR, workflow, or evidence.
4. Oracle/replacement compute runs heavy processing when needed.
5. Neon records structured status, provenance, checksums, and links.
6. Drive stores large durable artifacts when files are involved.
7. GitHub receives compact evidence and the final verdict.
8. Vercel changes only when the thin web/API surface actually changed.

## Decision rules

- If it is heavy, long-running, media-related, or compute-expensive, it belongs in Oracle/replacement compute.
- If it is structured truth, identity, status, provenance, checksum, or linkage, it belongs in Neon.
- If it is a large file, human-readable report, video, audio, PDF, export, or recovery artifact, it belongs in Drive.
- If it is source code, workflow logic, tests, policy, or compact evidence, it belongs in GitHub.
- If it is the public web/API entrypoint, it belongs in Vercel.

## Simplification decisions

These names are implementation details inside the five-block model, not separate top-level architecture blocks:

- Assistant Lab is part of compute.
- Universal Video is part of compute.
- DDS3 and BEN are compute engines.
- Release Controller is a future GitHub/operations dispatcher, not a required extra platform.
- Cloudflare Pages or Netlify may become preview fallback, but only as a Vercel-role replacement for web preview, not as a compute layer.

## Current priority

The next architecture work should keep the five-block model and reduce accidental cross-role coupling:

1. Stop unrelated changes from spending Vercel deployments.
2. Stabilize bounded Oracle status/start/stop as the manual compute control path.
3. Close video lifecycle durability gaps with Drive readback plus Neon checksum/status proof.
4. Add dispatcher/release-controller behavior only when it removes duplicated workflow logic.

## Non-goals

- Do not split the monorepo only to make the diagram prettier.
- Do not move heavy work to Vercel.
- Do not make Drive an operational database.
- Do not put large binary archives in Neon.
- Do not create new paid infrastructure without director approval.
