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

A Vercel build is relevant only for an approved thin web/API runtime release. `vercel.json` keeps the existing Git connection; `scripts/vercel_ignore_build.sh` permits Production only when `VERCEL_ENV=production`, `VERCEL_GIT_COMMIT_REF=main`, and the Vercel commit SHA exactly matches the non-secret Production environment variable `VERCEL_APPROVED_RUNTIME_SHA`. Preview requires `VERCEL_ENV=preview`, a nonempty ref other than `main`, and an exact match with the separate Preview variable `VERCEL_APPROVED_PREVIEW_SHA`. Missing or invalid metadata and an unset approval skip the build. The gate does not require `.git`, which `.vercelignore` excludes.

Automatic Preview deployments remain disabled by `vercel.json`. Compute rollouts are independent and must use their bounded compute-specific deployment path.

For explicit Preview acceptance, the deploy owner reviews a candidate with this gate, identifies its exact SHA and non-`main` ref, then sets `VERCEL_APPROVED_PREVIEW_SHA` only for the Preview environment. Confirm there are no unexpected `QUEUED`, `INITIALIZING`, or `BUILDING` deployments, and hold unrelated pushes during the approval window. Use **Create Deployment** from that Git SHA and select its Preview branch configuration. Require a **new deployment ID**, `VERCEL_ENV=preview`, the intended SHA, and the gate's approval log before accepting the Preview. If the SHA already has a canceled deployment and Create Deployment returns that old ID, use **Redeploy** on that exact deployment with **Use project's Ignore Build Step** enabled; allow one attempt. If neither path creates a new deployment through the gate, stop. Remove the Preview approval only after reviewing the active deployment queue; cancel unintended queued or in-progress attempts with the deploy owner. Removal affects future deployments, not deployments already created with the approval.

For an explicit Production release, review the runtime diff, CI, Preview evidence, and exact merge SHA on `main`. The merge's automatic deployment should be canceled while `VERCEL_APPROVED_RUNTIME_SHA` is unset. Confirm its SHA, canceled state, and gate log. Confirm no unexpected active deployments, then set `VERCEL_APPROVED_RUNTIME_SHA` only for the Production environment to that 40-character lowercase SHA and hold unrelated merges. **Redeploy** that exact canceled deployment with **Use project's Ignore Build Step** enabled; do not uncheck it or substitute a later commit. Require a **new deployment ID**, the intended SHA and Production target, the gate's approval log, and application acceptance checks. Allow one attempt; if Redeploy is unavailable or returns the old canceled deployment, stop and resolve the delivery path before another build. Remove the approval after inspecting and resolving all `QUEUED`, `INITIALIZING`, and `BUILDING` deployments. The removal cannot revoke a deployment that captured the approval earlier.

Removing either approval makes **new** deployments in that environment skip; it does not alter already created deployments. Coordinate all pending PR merges with the deploy owner: once this gate is active, no PR will automatically produce a full build. **Before merging this gate**, verify both approval variables are unset, and review the exact merge commit for `vercel.json` pointing to `scripts/vercel_ignore_build.sh` and `.vercelignore` including that script. Vercel documents file-based `ignoreCommand` as a per-deployment override, so the merge deployment should execute the new gate from its own commit and skip. Confirm that result in the deployment record before allowing later releases. If the previous gate runs instead, stop and investigate; do not incur another build to test a workaround. Temporarily selecting "Don't build anything" in Project Settings is not a bootstrap control because the commit's `vercel.json` overrides that setting. Disconnecting Git is not part of this procedure.

The existing `main` Git connection still starts an Ignored Build Step for each later merge; Vercel counts canceled builds toward deployment quotas and concurrent build slots. With unrelated merges held, introducing this gate and later releasing one runtime change entails two automatic canceled invocations (gate merge and runtime merge), one Preview build, and at most one Production Redeploy attempt: **four counted deployment attempts**. A separate gate-PR Preview or failed attempt needs its own additional allowance. Check current plan usage and agree on this bound with the deploy owner before any build; this does not use the book budget.

Before a Production build, record the currently aliased known-good deployment ID and its SHA, environment/configuration version, and domains without copying secret values. If the release fails acceptance, review the rollback target's original environment variables, possibly stale configuration, domains, and data compatibility before **Instant Rollback**. After rollback, verify the restored site and keep approvals unset. Vercel stops automatic assignment of Production domains after Instant Rollback; when a vetted deployment is ready, use **Undo Rollback** or promote that deployment, then verify normal domain assignment and the active queue. Instant Rollback does not rebuild with current environment variables.

## Heavy compute rule

Vercel is not a compute scheduler and must not execute DDS3/BEN/video batch workloads. The web layer may submit or inspect durable jobs, but execution belongs to the compute layer.

## Canonical research flow

`Chat / Research Lab -> ResearchJob -> Assistant Lab -> Compute -> DDS3/BEN/video -> Neon -> Artifact -> methodical derivative`

Automatic research output does not silently modify the teaching canon. Canonical promotion remains a separate reviewed action.

## Evolution path

The current monorepo remains canonical. Logical boundaries should converge toward `apps/web`, `services/*` and shared contracts without forcing a premature multi-repository split. A controlled Release Controller may later replace the current exact-SHA approval process after its release behavior has been proven in production.
