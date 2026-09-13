# Runtime access boundary

The production database uses a two-layer role model.

## Capability roles

These roles are always `NOLOGIN` and hold database privileges:

- `bridge_school_reader` — broad read-only school-data capability.
- `bridge_school_app` — interactive application capability.
- `bridge_school_worker` — background ingestion/analysis/projection capability.
- `bridge_school_health` — minimal technical health-only capability.

## Dormant principals

Migration `0016_runtime_principals` creates three principals:

- `bridge_school_app_principal`
- `bridge_school_worker_principal`
- `bridge_school_health_principal`

They are intentionally created `NOLOGIN`. Each inherits exactly one capability role. The migration contains no password and cannot activate database access by itself.

This means a database deploy may safely provision the authorization boundary before any application secret exists.

## Credential provisioning rule

A principal may be changed to `LOGIN` only when its credential is created in the external secret store used by the corresponding runtime. Never place a database password in:

- a migration;
- repository files;
- a commit message;
- a pull request body or comment;
- a GitHub Actions log.

Use a different credential for app, worker and health monitoring. Do not reuse the Neon owner connection string for runtime services.

A runtime database value must be a complete connection URI. Application runtime validation must verify the expected principal, expected database, a Neon host, TLS, and channel binding. Serverless application traffic must use the pooled Neon endpoint. Password-only fallback construction is not permitted in the production application runtime.

Production and Preview must use different Neon branches and separately scoped environment values. A production runtime value must never be attached to a Preview deployment.

## Intended activation sequence

1. Deploy migration `0016_runtime_principals` and verify CI/production migration checks.
2. Create a strong independent secret for the required principal outside Git history.
3. As the Neon database owner, set the selected principal to `LOGIN` and assign that secret.
4. Store the corresponding connection string only in the runtime secret store.
5. Connect as that principal and verify its effective permissions before starting the service.
6. Leave principals for services that do not yet exist as `NOLOGIN`.

The health principal deliberately cannot read student/person/source data directly. It can read only `database_runtime_fingerprint`, `operational_health_signal`, `operational_health_issue` and `operational_health_summary`.

## Current worker runtime

The current background video worker runs in GitHub Actions via `.github/workflows/bridge-video-3.1-free.yml`. It is therefore the first runtime that should receive an activated database principal.

Its runtime connection string is stored only as the GitHub Actions secret `BRIDGE_WORKER_DATABASE_URL`. Until that secret exists, the workflow leaves database access disabled and reports the database preflight as skipped.

When the secret is configured, `database/runtime_worker_preflight.py` connects before the worker starts and fails closed unless all of the following are true:

- the authenticated database user is exactly `bridge_school_worker_principal`;
- it inherits `bridge_school_worker`;
- it can read the school registry;
- it cannot update operational-health policy;
- it cannot delete person records;
- the canonical school seed `Школа спортивного бриджа` exists exactly once.

The preflight never prints the connection string or password.

## Security deployment algorithm v2.0

Every production runtime or database change follows this sequence:

1. Start from the current `main` commit and make the change on a dedicated branch.
2. Treat workflow files that can use credentials or OIDC as privileged code.
3. Do not interpolate GitHub event data or workflow inputs directly into shell source; pass them through environment variables and validate them as data.
4. Expose a secret only to the step that needs it. Install dependencies before cloud credentials are created whenever possible.
5. Pin third-party GitHub Actions to immutable full commit SHAs where practical.
6. Run the API on Python 3.12 in CI and production, and execute the DSN contract test before merge.
7. For a database change, create a new numbered migration; never edit an already-applied migration.
8. Run the clean PostgreSQL 18 migration suite, invariant tests, permission tests, migration idempotence test, and historical-checksum tamper test.
9. Merge to `main` only after required checks pass.
10. Promote the exact tested database commit to `database-production`; the production migration workflow repeats the disposable PostgreSQL 18 preflight before Neon is touched.
11. Serialize production migrations with the advisory lock, re-check migration state after locking, then verify registry checksums, runtime fingerprint, and operational health.
12. After deployment, verify the application health endpoint, runtime principal boundary, current migration key, zero missing checksums, zero critical health signals, and recent runtime error clusters.
13. Before any high-impact migration, verify the available recovery window and an appropriate restore/branch plan.

`main` and `database-production` are security boundaries. They must be protected against force-push and deletion, and production promotion should require pull-request review/status checks. A failure or absence of a required gate stops promotion.

## Recurring security audit checklist

Re-check periodically and after major infrastructure changes:

- branch/ruleset protection on `main` and `database-production`;
- dependency alerts and available code/secret scanning;
- mutable GitHub Action tags;
- shell interpolation of external inputs;
- step-level secret scope;
- runtime role attributes and privilege drift;
- PUBLIC schema/table/function grants;
- `SECURITY DEFINER` search paths and execute ACLs;
- production/preview environment isolation;
- Neon production-branch protection and public-network exposure;
- API authentication, rate limiting and error disclosure;
- dependency vulnerabilities;
- RLS requirements before any direct browser/Data API or multi-tenant database access.
