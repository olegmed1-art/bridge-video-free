# Canonical deployment architecture

This repository is a monorepo, but its runtime responsibilities are intentionally separated.

This deployment document implements the active top-level model in
[`simple_operating_architecture_v1.md`](simple_operating_architecture_v1.md):
Codex coordinates, GitHub controls, Oracle computes, Neon and Drive store,
Vercel shows.

## Responsibility boundaries

- **GitHub**: source code, pull requests, CI, release policy and audit trail.
- **Vercel**: thin Bridge School web/API runtime only (`app.py` + `bridge_school_api`).
- **Oracle or replacement compute**: Assistant Lab resident worker, DDS3, BEN, video processing, world generation and other heavy/batch work.
- **Neon Postgres**: durable ResearchJob state, structured results, provenance and compact metadata.
- **Drive/object storage**: source video/audio and large durable media artifacts.

## Deployment rule

A change to DDS3, BEN, Assistant Lab workers, video processing, research code, tests, docs, database migrations or operational workflows MUST NOT by itself create a Vercel deployment.

A Vercel build is relevant only when the thin web/API runtime changes. The repository enforces this with `scripts/vercel_ignore_build.sh` and `vercel.json`.

PRs that change the web/API runtime may receive Vercel Preview deployments. Changes merged to `main` may produce Vercel Production deployments. Compute rollouts are independent and must use their bounded compute-specific deployment path.

## Heavy compute rule

Vercel is not a compute scheduler and must not execute DDS3/BEN/video batch workloads. The web layer may submit or inspect durable jobs, but execution belongs to the compute layer.

## Canonical research flow

`Chat / Research Lab -> ResearchJob -> Assistant Lab -> Compute -> DDS3/BEN/video -> Neon -> Artifact -> methodical derivative`

Automatic research output does not silently modify the teaching canon. Canonical promotion remains a separate reviewed action.

## Evolution path

The current monorepo remains canonical. Logical boundaries should converge toward `apps/web`, `services/*` and shared contracts without forcing a premature multi-repository split. A controlled Release Controller may later replace direct platform Git deployment once the current path-aware deployment gate has been proven in production.
