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

A change to DDS3, BEN, Assistant Lab workers, video processing, research code, tests, docs, database migrations or operational workflows MUST NOT by itself create a Vercel production build.

A Vercel build is relevant only for an approved thin web/API runtime release. `vercel.json` keeps the existing Git connection to `main`; `scripts/vercel_ignore_build.sh` permits a build only when `VERCEL_ENV=production`, `VERCEL_GIT_COMMIT_REF=main`, and the Vercel commit SHA exactly matches the non-secret Production environment variable `VERCEL_APPROVED_RUNTIME_SHA`. Missing or invalid metadata and an unset approval skip the build. The gate does not require `.git`, which `.vercelignore` excludes.

Preview deployments are disabled. Every PR merge to `main`, including a web/API change, skips the full build until its exact merge SHA is approved. Compute rollouts are independent and must use their bounded compute-specific deployment path.

For an explicit web/API release, the deploy owner must review the runtime diff and CI result, identify the exact commit on `main`, then set `VERCEL_APPROVED_RUNTIME_SHA` to that 40-character lowercase SHA in the Vercel project's Production environment. In the project's Deployments page, use **Create Deployment** with that exact Git SHA and select the `main` branch configuration if Vercel asks. Confirm the project's Ignore Build Step remains active, the gate reports approval, the resulting Production deployment has the intended SHA, and application acceptance checks pass. Remove the approval variable after the release. Do not substitute a later commit or bypass the gate if this exact-SHA path is unavailable; resolve that delivery limitation before release.

The setting is reversible: removing `VERCEL_APPROVED_RUNTIME_SHA` makes subsequent builds skip again. Coordinate the one-time rollout of this gate and all pending PR merges with the deploy owner: verify this approval variable is unset before merging the gate, and no current PR will auto-deploy after it. The existing `main` Git connection still starts an Ignored Build Step for each merge; Vercel counts canceled builds toward deployment quotas and concurrent build slots. Agree on that usage before delivery. A rollback of an already published production deployment needs the normal production rollback decision and verification.

## Heavy compute rule

Vercel is not a compute scheduler and must not execute DDS3/BEN/video batch workloads. The web layer may submit or inspect durable jobs, but execution belongs to the compute layer.

## Canonical research flow

`Chat / Research Lab -> ResearchJob -> Assistant Lab -> Compute -> DDS3/BEN/video -> Neon -> Artifact -> methodical derivative`

Automatic research output does not silently modify the teaching canon. Canonical promotion remains a separate reviewed action.

## Evolution path

The current monorepo remains canonical. Logical boundaries should converge toward `apps/web`, `services/*` and shared contracts without forcing a premature multi-repository split. A controlled Release Controller may later replace the current exact-SHA approval process after its release behavior has been proven in production.
