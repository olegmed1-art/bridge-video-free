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

Its future runtime connection string is stored only as the GitHub Actions secret `BRIDGE_WORKER_DATABASE_URL`. Until that secret exists, the workflow leaves database access disabled and reports the database preflight as skipped.

When the secret is configured, `database/runtime_worker_preflight.py` connects before the worker starts and fails closed unless all of the following are true:

- the authenticated database user is exactly `bridge_school_worker_principal`;
- it inherits `bridge_school_worker`;
- it can read the school registry;
- it cannot update operational-health policy;
- it cannot delete person records;
- the canonical school seed `Школа спортивного бриджа` exists exactly once.

The preflight never prints the connection string or password.
