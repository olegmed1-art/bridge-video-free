# Incident #1911: protected runtime diagnostic

Status: diagnostic candidate; not a recovery receipt or production approval.
Baseline main: `7a43310d18aa8c3d8ebf10d6a07f3f9996195b5c`.
Baseline production deployment: `dpl_FfpA8tewDhmja6X55b1YUDPj2nAA`
(READY, but `/healthz` still 503 with `authentication_failed`).

## Execution path

The implementation lives in `bridge_school_api/incident_db_probe.py`, which is
included by `.vercelignore`. The existing `/healthz` failure handler calls
`probe()` without connection opt-in and records its fixed status and booleans
in protected runtime logs. No new HTTP endpoint is added. The existing health
check remains the real application connection attempt; config logging adds
zero DB connections or SQL statements and keeps the public 503 generic.

`database/` is excluded from Vercel packaging. Its CLI wrapper is for a separately
approved protected executor with the repository present; it is NOT the Vercel
execution path. No remote shell in an existing Vercel function is assumed.

```sh
python -m bridge_school_api.incident_db_probe
python -m bridge_school_api.incident_db_probe --connect
```

The optional CLI `--connect` performs one connection, one read-only SELECT of
session identity/settings and explicit rollback, with 10-second connection and
5-second statement timeouts. It is not invoked by the API logging integration.
No live CLI execution is authorized by this document.

## Interpreting the evidence

Output never includes a URI, password, hostname, credential hash or arbitrary
exception text. The `source` object contains only these booleans:

- `raw_host_is_expected`: saved URI authority is the pinned production pooler
  or its direct counterpart.
- `raw_host_is_pooler` / `raw_host_is_direct`: distinguish the two known forms.
- `endpoint_was_rewritten`: application normalization changed URI authority.

The application currently canonicalizes any Neon authority to its pinned host.
An unrelated source can therefore have `endpoint_matches=true` after
normalization; it now yields `source_endpoint_requires_review` and the optional
CLI will not connect. An expected direct-to-pooler rewrite remains valid.
Query overrides are separately checked against effective libpq parameters.

`configuration_pass_connection_not_tested` validates configuration only. It
proves neither password validity nor endpoint-to-branch mapping, data integrity,
permissions or recovery. `libpq_environment_requires_review` is conservative;
it is not proof that an ambient PG variable caused the incident. Never delete
configuration based on that result alone.

## Bounded observational release

1. Pin the reviewed candidate commit, CI evidence and independent I2 review;
   reconcile fresh main and the live Vercel alias immediately before release.
2. Reconcile intended production endpoint mapping against incident #1912 using
   read-only metadata. Recording config does not require a DB cutover. Preserve
   the separate writer-fencing and data-reconciliation gates.
3. Retain the current deployment identity/configuration for code rollback. It
   is an unhealthy baseline, so rollback must not be called availability recovery.
4. Select this diagnostic-only candidate for the release. `vercel.json` enables
   Git builds only for main; `scripts/vercel_ignore_build.sh` triggers a build
   for changes under `bridge_school_api/`. A reviewed main merge therefore
   changes production code; a feature-branch push alone does not deploy it.
5. After an authorized release, read back READY state, exact source SHA and
   alias; then request `/healthz` once and collect the protected log for that
   exact deployment/request. The response contains no diagnostic details.
6. Configuration evidence plus `authentication_failed` narrows further protected
   role/credential investigation. Fix only a confirmed mismatch through scoped
   recovery. Do not infer that changing code has repaired authentication.

Abort/reassess on a changed target, unexpected diff, missing review or CI,
unrelated production deployment, new public diagnostic output, or a new import
failure. A code regression can be reversed through the reviewed deployment
rollback or a revert of this diagnostic patch, without changing credentials,
roles or Neon topology. Verify the result after rollback.

Recovery needs `/healthz` HTTP 200 with `{"status":"ok"}` and affected-path
checks. Light/P0 HOLD, original single-send lineage and incident #1912 remain
separate gates. This patch performs no role, secret, branch, endpoint, alias,
service-state, migration or canon mutation.
