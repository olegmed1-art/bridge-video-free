# Native permission maintenance: focused writer classification

2026-09-26 UTC; ASSURED / preparation only; tracking #1946.
Reviewed main: `a3e5b10efd9c7ff24c6ed1301e17e58b7548a8ae`.
This reviews seven selected workflow candidates from #1969, not the entire
inventory. It separates current checked-in behavior from credential capability
and live deployment state. No permission to run any workflow follows from it.

## Source review results

| Workflow | Reviewed behavior and destination | Maintenance consequence |
| --- | --- | --- |
| autopilot-source-migration-preflight.yml | Calls oracle_autopilot_source_preflight. Rebuilds connection parameters for a fixed Neon host/neondb; read-only connection and transaction checks; catalog SELECTs and rollback. | Read-only source path in this revision. It need not be treated as an intentional ACL writer solely because it consumes an owner credential. Credential reuse, changed code and historical runs remain separate. |
| neon-independent-backup-restore.yml | Source statistics SELECTs and pg_dump; pg_restore explicitly uses 127.0.0.1:5432/recovery in a disposable CI service. | Restore target is not the source Neon database in the reviewed code. Source credential privileges and availability/lock effects still require separate consideration. |
| oracle-light-fresh-candidate.yml | Fixed read-only Neon source/exported snapshot; CI restore; then remote restore into a name matching autopilot_candidate_fresh_[0-9]{6,20} in the Oracle container. | Other-database/host writes, not a reviewed Neon ACL mutation. It still consumes Oracle host resources; do not run or exempt it from host coordination merely on this classification. |
| recovery-registry-population.yml | record_recovery_evidence writes public.recovery_checkpoint (upsert) and public.recovery_verification (insert) using NEON_DATABASE_URL. Checks Neon suffix and neondb, not exact project/branch/endpoint. | Actual database-writing code outside the two proposed groups. Its current secret destination and trigger/dependency closure are not proven here. Keep it in operator coordination until scoped exclusion is justified. |
| autopilot-codex-event-callback.yml | Routes ack/terminal/publication through github_autopilot_db_route and the server lease. Route can select Neon, Oracle or paused. | Runtime writer requiring quiescence, independent of the owner ACL group. Current route installation/value were not inspected on the host. |
| autopilot-temp-neon-owner-preflight-once.yml | Derives an ep-floral-field-b1pjs2of destination and applies 0306. Expected branch text is printed; actual branch identity is not queried. | Intended temporary writer. Control-plane mapping below confirms a separate branch now, but preserved source-URI query options and historical revisions prevent an unconditional exclusion. |
| autopilot-temp-neon-0308-structured-artifact-once.yml | Derives the same temporary endpoint, compares declared branch strings and applies 0308. | Same current separate-branch mapping and remaining connection-binding limits. No temporary-branch or production mutation was performed during this audit. |

The two read-only source paths and the other-database restore path are not
blanket capability exemptions: owner/SSH credentials remain privileged assets.
These classifications apply only to the reviewed code, normal trusted tool
execution and the specified destinations. No secret value was retrieved to
verify the current destination of a secret-derived DSN.

A fresh authenticated endpoint metadata read confirms ep-floral-field-b1pjs2of
belongs to br-still-tooth-b1ilkfcj in project misty-poetry-18012774, distinct from
the selected production branch br-aged-mud-b1i64914. This is a point-in-time
control-plane mapping, not a test of either workflow's actual SQL connection.
Both URI-building snippets retain parsed.query, so the review must also resolve
connection-option overrides before claiming the derived hostname is authoritative.

## Fresh read-only Neon evidence

The observation was explicitly READ ONLY with a five-second statement timeout.
Server settings identify project misty-poetry-18012774, branch
br-aged-mud-b1i64914 and endpoint ep-noisy-pine-b1pe30sf.

The callback, worker, health and Light application logins observed here lack
superuser, CREATEROLE, CREATEDB and BYPASSRLS flags. This does not imply they
lack writes through SECURITY DEFINER functions or inherited privileges.

For autopilot_callback_login, effective EXECUTE is true on
accept_role_dispatch_codex_ack, accept_role_dispatch_codex_terminal and
authorize_codex_publication, and false on all seven native_cli functions.
Read-only inspection of the live definitions confirms that ack inserts into
role_dispatch_codex_delivery_proof and updates role_dispatch_outbox. Terminal
inserts role_dispatch_codex_terminal_receipt and evidence, updates step_attempt,
task and role_dispatch_outbox, and calls record_event. No RPC was invoked.
authorize_codex_publication was not classified as a writer merely from its name
or EXECUTE grant; its inspected body supplies authorization checks.

Thus callback credentials cannot be ignored just because native CLI RPCs are
denied. The permission engine's existing locks cover several affected state
tables, but do not prove callback quiescence or cover every receipt/proof table.
A callback waiting on a table lock could continue after the grant transaction
commits. Table locking is not a replacement for stopping admission for the whole
maintenance/postcheck window.

## Existing coordination primitive and its limits

oracle_light_route_lease exposes a shared flock on a root-owned, inode-checked
route.lock while a consumer lease is active. A paused route returns without
starting the consumer. github_autopilot_db_route checks lease liveness while the
consumer runs and terminates its process group on loss/timeout.

This existing mechanism is a candidate for coordinating routed callbacks; do
not introduce a second unrelated lock and assume it covers them. A future
coordinator must verify the installed implementation, acquire the same lock
exclusively, and prove that prior consumers and their DB transactions have
finished. Lease loss/process cancellation alone is not proof of transaction
outcome or zero races. Direct owner SQL, unrouted consumers and other credential
holders do not participate in this file lock.

No server command, route write, lock acquisition, process stop, workflow dispatch
or production data/ACL write was performed for this classification.

## Concrete remaining work

1. Verify live route/process identity and prove callback drain under the existing
   lease protocol in a disposable rehearsal before proposing host operations.
2. Reconcile remaining privileged candidates, credential destinations, historical
   active/queued runs and direct owner channels. Scope exclusions need evidence;
   do not require every unrelated school workflow to stop by default.
3. Combine the applicable GitHub exclusions, routed-consumer drain and explicit
   direct-owner coordination in the grant/postcheck window. The current default
   MaintenanceGuard continues to reject apply.

The goal is bounded exclusion of relevant writers, not indefinite inspection of
every unrelated subsystem. The source and live catalog evidence above narrows
the next work to actual authority/state conflicts and unresolved destinations.

## Pinned source evidence

All named files are read at the baseline commit above. Their Git blob IDs are
recorded in NATIVE_PERMISSION_WRITER_CLASSIFICATION_SOURCES.json so future
reviews can detect source drift. The record is provenance, not an executable
allowlist, current endpoint mapping, complete transitive review or maintenance
authorization. Re-read changed dependencies and current primary sources before
any production stage transition.
