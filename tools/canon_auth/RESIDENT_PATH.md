# Resident connection path: inventory and isolated experiment

Inventory base: current main efe59fc153aca09d83b8d5b4f9595d0fb2dc5e73.
The existing candidate f6149671 is based on 08cd157b; fresh integration and
deployment pins remain mandatory before any later live attempt.

| Existing path | Exact preflight / revoke capability | Result |
|---|---|---|
| Vercel API / existing app connection | Normal teacher HTTP and SQL reads. App-role binding is enforced by bridge_school_api/db.py. 0200 part06 explicitly revokes runtime_activation writes from app/worker. | Cannot revoke; do not add an API mutation route or change role. |
| Existing Oracle/worker/autopilot clients | Scoped worker/callback/native-maintenance operations; route/HOLD and identity controls remain in force. | Their purpose and restricted SQL are not generic canon execution. Do not repurpose them or lift HOLD. |
| Existing protected owner workflows | database-production and native-maintenance-owner-attest already consume NEON_DATABASE_URL (or the existing maintenance fallback) inside the runner. Owner attestation binds neondb_owner, TLS, project/branch/endpoint and read-only transaction. | Existing owner credential contour is a candidate; current credential validity and canon privileges are unproven. No supported fixed canon executor/dispatch tool is exposed here. |
| Vercel sandbox session command | Can execute only in an existing sandbox session. Read-only list_sessions returned no sessions for this project. | No resident authenticated production owner connection; do not create one or inject/copy secrets. |
| Neon MCP | Existing generic SQL/transaction path. | Unknown tool technical blocker; not retried in this inventory. |

## Minimal path without depending on Neon MCP

Use the already authorized owner credential in its existing protected runtime,
without returning it to this agent or creating a new key. A separate fixed-scope
canon operator can consume the existing compiler's exact transactions on that
dedicated owner connection. It must not extend the API, worker, maintenance
manifest or old migration client.

Before deployment/activation: prove that this runtime has a supported invocation
channel and valid owner connection; perform immutable Neon server identity checks
and all exact plan privileges, including UPDATE status/valid_to on BOTH activation
tables. Then prove pilot absence, source/schema/gates, current main/READY and the
authenticated API baseline. Preserve independent request-ID log correlation
between each bounded transaction. No new credential, GRANT, ACL, browser login,
queue job, arbitrary-SQL API, or modification of an old workflow is implied here.

The credential contour exists in source, but an accessible, qualified owner
execution channel has not been proven. Available GitHub tools expose run/job
reads and reruns, not workflow_dispatch. An old-run rerun or migration workflow
is not a supported canon operator. Therefore the minimum live blocker is owner
execution/accessibility and fresh privilege proof, not a renewed pilot permission.

## Code and evidence

resident_preflight.inspect_resident accepts an already opened connection and
fresh trusted Binding. It reads no env, DSN, password or credential fingerprint,
opens no connection, and makes no role/ACL change. A dedicated idle connection,
TLS, exact owner identity and immutable pg_settings binding are required. Output
contains capability booleans only and explicitly does NOT admit writes. Missing
privilege stays missing; no self-elevation.

resident_rehearsal.revoke_on_failure is restricted to the existing loopback
tournament_rehearsal DB. It recomputes the fixed reviewed SQL plan, refuses to
start its body without revoke capabilities, and commits an owned-only emergency
revoke after an injected failed evidence gate. It cannot be used as a production
runner. The existing PG18 CI rehearsal also proves the actual app role cannot
revoke, normal 40-row lifecycle, failure-triggered revoke to ABSTAIN, maximum42,
idempotent emergency and preserved original expiry.

No finally block guarantees immediate revoke if the process dies, the network
fails or the database is unavailable. Tests explicitly return
emergency_revoke_unproven in that case. Production needs a reachable owner
recovery channel, plus the existing server-enforced original TTL as bounded
exposure. TTL expiry removes eligibility; it is not proof of a persisted revoke
or substitute for a successful rollback/readback.

Production SQL/build/merge/deploy were not performed for this inventory.
