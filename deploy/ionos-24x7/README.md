# IONOS Cube XL 24/7 deployment

Status: **design / not activated**.

This directory implements the deployment contract recorded in issue #265. It does not provision IONOS resources and must not be used for production cutover until the acceptance gates are complete.

## Target topology

- Vercel `fra1`: public web/control API.
- Neon `eu-central-1`: canonical PostgreSQL state, queue, checkpoints and evidence.
- IONOS Cloud Basic Cube XL in Frankfurt: 24/7 compute plane.
- GitHub Actions: CI, regression, evidence and security only.
- Google Drive: original videos and durable derived artifacts.

## Compute services

The Cube hosts long-lived containers for:

- DDS3 runtime with persistent in-memory `SolverContext` / TT;
- BEN;
- Bridge AI worker;
- later: provider-neutral video/Whisper, vision and batch workers after their durable claim/receipt contracts exist.

The raw DDS3 and BEN container ports are private. Public ingress is only through the authenticated HTTPS gateway.

## Host layout

Create these directories on the Cube:

```text
/srv/bridge/
  compose/
  env/
  models/
  cache/
  work/
  logs/
```

The 960 GB local NVMe is working storage/cache. It is not a canonical store. Original video, final transcripts, canonical evidence, queue state and checkpoints must remain in Drive/Neon or another durable approved store.

Recommended disk thresholds:

- 70%: warning;
- 80%: stop staging new heavy jobs;
- 90%: incident / emergency cleanup.

## Deployment inputs

Copy `env.example` to a root-owned runtime environment file outside Git, for example `/srv/bridge/env/production.env`, chmod `0600`, and populate it with real values.

Production image references must be immutable/pinned. Do not use floating tags such as `latest` for DDS3 or BEN at production cutover.

## Startup

From `/srv/bridge/compose`:

```bash
docker compose --env-file /srv/bridge/env/production.env up -d
```

All long-lived services use `restart: unless-stopped`. Docker must be enabled at boot.

## Safety boundaries

- No secrets in Git.
- No arbitrary PR code on the production Cube.
- One production queue consumer at a time.
- Worker fails closed if its verified engine is unavailable.
- Video auto-discovery remains disabled; only explicit opaque jobs are allowed.
- Do not disable the existing GitHub production worker until the IONOS consumer has passed acceptance and observation.

## Acceptance before cutover

Run `acceptance.sh` from a trusted administration host after DNS and the compute token are configured.

Required evidence:

1. HTTPS gateway reachable and raw DDS3/BEN ports not public.
2. `/readyz` reports real DDS3 readiness and `fallback_used=false`.
3. authenticated golden DD-table call succeeds.
4. repeated `position_all_moves` proves same live TT instance and lower repeat node count.
5. BEN health/bid call succeeds privately.
6. Bridge AI worker drains a non-destructive canary correctly.
7. host reboot restores services automatically.
8. queued durable work survives restart through Neon.
9. DDS/BEN latency remains acceptable while video workers are saturated.

Video migration remains a separate gate until a provider-neutral durable video queue/receipt worker is implemented.
