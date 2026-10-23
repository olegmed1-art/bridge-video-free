# Autopilot database client closure (2026-09-24)

Status: **STOP for cutover**. Scope: Autopilot production and shadow data only. This is a code inventory at `7191e543abb8e9c7603ba73de701889d70f21c47`; it does not prove which remote clients or credentials are currently live. No route, source fence, or service was changed by this review.

## Routed clients on main

`ops/github_autopilot_db_route.py` accepts a server route lease and runs nine targets on trusted `main`: `role-callback`, `codex-ack`, `codex-terminal`, `codex-publication` (callback principal); `diagnostics`, `reconcile`, `next-step` (worker principal); `health` (health principal); and `mailbox` (callback principal). Their entry points are `autopilot-chatgpt-role-callback.yml`, `autopilot-codex-event-callback.yml`, `autopilot-paused-reconcile.yml`, `autopilot-reconcile-diagnostic.yml`, `database-health-monitor.yml`, and `autopilot-mailbox-pre-rotation.yml`. The latter two run hourly. `database-health-monitor.yml` also calls the *school* app URL and worker preflight with school database credentials. Those checks and the school's `BRIDGE_APP_DATABASE_URL` must remain available independently of Autopilot cutover.

The lease changes the nine selected process DSNs to a pinned PostgreSQL tunnel only for backend `postgresql`; for backend `neon`, source DSNs remain in use. It does not revoke Neon credentials, drain existing sessions, or govern other workflows. `AUTOPILOT_DB_BACKEND` defaults to `neon` in `oracle_autopilot/database_target.py` if a process bypasses the lease.

## Paths outside that lease

| Path | Code-level trigger and credential | Cutover consequence |
| --- | --- | --- |
| Light worker and observer | `oracle_autopilot/worker.py`, `online_observer.py`; systemd environment `AUTOPILOT_DATABASE_URL` | `validate_neon_direct_dsn` accepts pinned Oracle PostgreSQL only when `AUTOPILOT_DB_BACKEND=postgresql`; otherwise it requires direct Neon. Check installed environment and actual sessions, including production *and* shadow, before promotion. |
| Legacy heavy production canary | `.github/workflows/oracle-autopilot-production-canary.yml`, push on `codex/oracle-autopilot-lite-shadow`; uses `NEON_DATABASE_URL` directly and heavy IP `158.180.47.161` | A historic branch push can reach the old owner path; inability to reach the retired host is not proof that owner access is fenced. Confirm branch/workflow enabled status and disable/retire or guard the owner action before cutover. |
| Legacy role hardening | `.github/workflows/autopilot-runtime-role-hardening.yml`, exact owner issue comment on #1131; uses `NEON_DATABASE_URL` directly | Even from current main, an owner command can mutate the source and provision old runtime credentials. Stop this trigger or change it to a cutover-aware command before fence. |
| Legacy canary cleanup | `.github/workflows/autopilot-production-canary-cleanup-once.yml`, historic branch push; uses `NEON_DATABASE_URL` | Check workflow/branch active state and in-flight runs; no assumption from main-only scans. |
| Manual source probes and migrations | `autopilot-readonly-github-schema-once.yml`, `autopilot-source-migration-preflight.yml`, `ops/oracle_autopilot_source_preflight.py` | Read-only preflight may remain during transition. Distinguish its owner credential from all writer paths; do not infer global read-only status from a read-only client session. |
| Other branch request workflows | `.github/workflows/oracle-autopilot-online-observer.yml`, `oracle-autopilot-online-resume.yml`, `oracle-autopilot-shadow-activation.yml`, `oracle-autopilot-staging.yml`, `oracle-autopilot-rollout.yml` | Historic branch or issue-comment actions can install/reset services using independent env files. Inspect active workflow state, exact branch head, outstanding runs and installed systemd units. |
| Publication permit helper | `oracle_autopilot/github_codex_publication_permit.py` refers to `AUTOPILOT_OWNER_DATABASE_URL` | Repository search finds no workflow invoking it; establish deployment or manual invocation status instead of claiming it cannot write. |
| Vercel school API and health | `bridge_school_api/db.py`, `database-health-monitor.yml` | This is a separate school database contour. Inventory actual Vercel environment and Neon dependencies; a cutover of Autopilot alone must not disable it. |

## Necessary primary-source proof immediately before cutover

1. Reconcile fresh `main`, open PRs, default-branch and historic-branch workflow enabled states, recent and in-flight Actions runs (including dispatch, issue comments and scheduled runs), deployed Light units, process environments with secrets redacted, active `pg_stat_activity` by database/user/application, actual DSN host and route epoch. Do not log passwords or full URIs.
2. Maintain an allowlist of every Autopilot principal: production and shadow runtime, callback, worker, health, Neon owner, any legacy principals, maintenance and manual accounts. Identify each password, service, tunnel, GitHub environment, and branch trigger. Unknown principal or unclassified write-capable client is a STOP condition.
3. Under one cutover lock, block *new* source writes for **each** Autopilot-capable credential, drain or terminate pre-existing writer sessions, and prove denial with each credential. A grant/revoke alone does not prove existing sessions gone; a disconnected host does not prove Neon credentials are safe. Keep the school's unrelated roles and data untouched.
4. While fenced, take final consistent source snapshots of both production and shadow, restore isolated targets, compare schema, row counts, sequence values, privileges and selected high-value identities. Verify external backup recovery separately.
5. Switch every classified client to Oracle, read back deployed DSNs without secret values, prove route epoch and database identity, then execute one bounded real task with ACK and terminal receipt. Monitor source for attempted/new writes through the rollback window. Any orphaned writer or mismatch means remain paused and roll back from the preserved snapshot/route.

The nine-target lease and a successful source credential *read-only probe* are useful evidence, but neither proves closure of owner, manual, branch, old session or service paths. Never claim Neon independence until both Autopilot databases and every live client have independent proof.

The static inventory check matches literal `secrets.NEON_DATABASE_URL` references and exact route commands in the checked-out workflows. Secret aliases, reusable workflows, non-workflow entry points, older revisions and already-running jobs require the independent primary-source review above; a passing static check cannot authorize cutover.
