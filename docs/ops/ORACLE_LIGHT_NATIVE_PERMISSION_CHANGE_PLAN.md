# Oracle Light: bounded native permission change plan

Status: ASSURED / PREPARATION ONLY / NOT APPROVED FOR PRODUCTION APPLY.
Date: 2026-09-25 UTC. Tracking: #1946. Baseline main:
`e12c74d58f8564a9fad2032abfadbb1ecbf1f874` (freshly checked).

This plan supplements the historical activation proposal in #1955. It creates
no database permissions, enables no provider, and authorizes no task execution.
HOLD and the empty shared queue remain required. CLI installation is complete;
reinstallation and Node permission changes are not part of this next step.

## Evidence and limits

| Evidence | Conclusion | Limit |
| --- | --- | --- |
| Owner executed exact reviewed #1962 head d4003a18b4c5f6c62f5de6a1d91965496806157a | Full pre/post HOLD attestation passed; queue_nonterminal=0; live read-only identity verified | Point-in-time observation, not a perpetual live guarantee |
| Same audit | All seven native functions deny runtime EXECUTE; schema USAGE exists; native tables deny SELECT and writes | Detailed ACL and identity inventory stays in the protected operator record |
| Owner Neon read-only query, two screenshots supplied 2026-09-25 UTC | Exactly one returned enabled value is false; native_receipts_total=0 | Does not count unrelated Codex cloud tasks or replace a fresh shared-queue attestation |
| Owner executed #1961 cloud probe | Service CLI authenticated; cloud list read succeeded; no tasks started | Probe did not discover an environment ID or verify repository binding |
| Owner UI screenshots | Environment maps to the intended repository | UI evidence only; not yet service-account programmatic binding proof |
| #1957, #1959, #1960 | Disposable six-RPC rehearsal and dormant adapter/provider merged | No live deployment, production loader, or bounded pilot proven |

Audit source: https://github.com/olegmed1-art/bridge-video-free/pull/1962
Cloud source: https://github.com/olegmed1-art/bridge-video-free/pull/1961
Fixture: `database/fixtures/native_cli_runtime_acl.sql` at the baseline main.
SQL source: `database/migrations/0339_autopilot_native_cli_receipts.sql`.

## Exact proposed privilege delta

Recipient: only the existing Light login verified by the live-process audit,
resolved from the protected identity manifest. Do not substitute a shared role,
an owner identity, or a newly created login. No membership changes.

Add direct EXECUTE, without grant option, on exactly:

| Function signature | Purpose |
| --- | --- |
| autopilot.native_cli_reserve(uuid,jsonb,text) | Reserve one admitted dispatch |
| autopilot.native_cli_snapshot(uuid) | Read that session owner's receipt |
| autopilot.native_cli_current(jsonb) | Check current authority |
| autopilot.native_cli_begin(jsonb) | Record one-shot creation intent |
| autopilot.native_cli_ack(jsonb,text,text) | Record the existing provider task |
| autopilot.native_cli_finish(jsonb,text,jsonb) | Record terminal evidence |

Schema USAGE already exists: proposed delta is zero. Keep the private
`native_cli_authority_locked(uuid,jsonb)` denied. Keep native table privileges,
PUBLIC privileges, default privileges, memberships, owners, search_path,
function definitions and role/database settings unchanged. Preserve the current
HOLD listener and read-only audit sessions; this is not proof that every session
opened by the login is forced read-only by database policy.
Keep enabled=false, cutover_at unchanged, HOLD, service release/invocation,
credentials and scheduling unchanged. No native RPC is called for validation.

## Required apply-package preconditions

This document is not a copy-paste apply script. An immutable reviewed apply
package must enforce the following before it can commit any grant:

1. Reconcile fresh main and the exact reviewed package hash. Fresh full live
   attestation must verify HOLD, database READ_ONLY_PASS, same live DSN,
   queue_nonterminal=0 and the expected process identity/invocation.
2. Independently reconcile all seven observed pg_get_functiondef fingerprints
   with the pinned migrations reconstructed in a disposable PostgreSQL instance.
   Observed hashes alone are not approved reference hashes. Verify migration
   dependencies and relevant authority/terminal triggers, not only row markers.
3. Verify the exact database/branch and owner session through the authorized
   owner channel; keep owner credentials out of runtime and public artifacts.
   Record server version, function identities/OIDs, owners, language, volatility,
   SECURITY DEFINER, search_path, effective privileges, explicit ACL entries,
   schema ownership/ACL, and the recipient's inherited privilege closure.
4. Capture a protected before-manifest. Require one config row, enabled=false,
   zero receipts, all six target EXECUTE privileges absent, private helper denied,
   table access denied, schema USAGE already present, and no unexpected role
   capability. Any drift stops; do not repair drift inside the grant transaction.
5. Serialize the maintenance window against configuration, function, role/ACL
   changes and other operators. A lock used only by this script is insufficient.
   Review the actual exclusion mechanism; do not assume HOLD serializes DDL.
6. In one bounded owner transaction, with lock/statement timeouts and client
   stop-on-error, recheck the manifest, add only the six grants, and verify the
   exact post-ACL delta before COMMIT. On error ROLLBACK the transaction.
7. After COMMIT, run metadata-only checks using the runtime identity and fresh
   full HOLD attestation. Verify six EXECUTE=true, helper/table access still
   denied, original identity and read-only connection unchanged, config unchanged,
   receipts=0, queue=0 and unchanged service invocation. Do not call native RPCs.

The apply package must be rehearsed using actual PostgreSQL with a role matching
the relevant inherited permissions. Include wrong database/role, changed hash,
preexisting grant, changed config, existing receipt, post-check failure and
concurrent-change rejection. Record before/apply/rollback ACL equality.
Existing #1957 tests establish RPC lifecycle behavior, not this production
grant/rollback package or the required concurrency control.

## Rollback contract

Before COMMIT: rollback the transaction; no partial grants survive.

After COMMIT, before any task exists: under the same reviewed maintenance
exclusion, compare current state with the recorded exact post-manifest. Revoke
only the six newly added direct EXECUTE entries from the recorded recipient,
without CASCADE. Existing/inherited privileges must remain byte-for-byte or
semantically equivalent to the before-manifest. Refuse automatic rollback if
unexpected grants, dependencies, config changes or receipts appeared. Verify
original effective denials, unchanged config/release, queue0 and fresh HOLD.
This grant-only change needs no config rewrite or service restart to undo.

If provider creation may have occurred, preserve all receipts, journals and
credentials, contain new admission, and reconcile the original provider ID.
Do not reset intent, delete receipts, retry creation, or blindly revoke recovery
access. Such a state is outside this pre-task rollback and needs its own review.

## Remaining pilot gates

The observed HOLD listener and audit sessions are read-only. This does not
establish a role-level or database-enforced read-only restriction for every
connection opened by the runtime login. These SECURITY DEFINER functions can
write receipts, outbox and task state when their predicates are satisfied:
EXECUTE is a controlled write capability even without direct table privileges.
The verified disabled configuration and zero receipts keep that capability
dormant at the observed state. A READ_ONLY repository task still needs writable
bookkeeping sessions. Opening an adapter session for writes and changing
enablement/admission belong to a separate reviewed activation package.

Implement/review the serialized one-item production loader: exact existing
shared-admission dispatch, immutable repository/head/environment target,
READ_ONLY/VERIFY constraints, no arbitrary claim or second scheduler, and no
side effects under HOLD. A cooperative gate callback alone is not an atomic
stop guarantee. Prove authority fencing and one-item capacity with a fake
provider before a cloud task is authorized.

The exact release deployment/restart, enablement/cutover values, admission
transition, recovery path and launch decision remain separate gates. Do not
open the continuous queue as a substitute for a bounded pilot. Re-attest the
new process after any future restart; an unchanged-invocation check cannot
prove a restart succeeded. No pilot or HOLD release is authorized by this plan.

## Reconciled preparation and maintenance design — 2026-09-26

Primary source baseline: `8b302b23b262504d9148d76a66f227e12d540a6f`.
This section updates preparation evidence only. Production apply remains
unimplemented and unauthorized by this document.

Completed repository work:
- #1965 merged as `b478e1ec7a1e9d116711ca1c2f063f3c906374ad`: full
  per-function inventory; identical pre/post-test catalog; all nine live
  definition differences explained; observed extra outbox trigger successfully
  rehearsed without changing production. Its installation provenance remains
  unknown and it must be preserved.
- #1966 merged as the baseline above: non-superuser owner grants six entries
  across COMMIT, new backend verification, drift-refusing rollback, explicit
  revoke across COMMIT, original state restored. Exact-head database evidence:
  https://github.com/olegmed1-art/bridge-video-free/actions/runs/36213165865
- That test also demonstrates a privileged ACL writer succeeding while the
  grant connection holds native table locks. Table locks cannot stand in for
  the maintenance exclusion.

A fresh read-only catalog observation found all six native RPCs and the helper
denied to the Light login, native table/column access denied, schema USAGE
present, and zero nonterminal tasks. Application logins could not SET ROLE to
the database owner. This is neither complete privileged-operator discovery nor
an attestation that all SECURITY DEFINER calls are read-only. Managed-provider
administrative identities remain a platform trust boundary; do not disable or
alter them as part of this recovery.

### Observed entry points (bounded map, not a complete allowlist)

Paths below refer to `.github/workflows/` at the baseline.

| Entry point | Existing exclusion | Consequence for the proposed window |
| --- | --- | --- |
| `database-production.yml` | Authorized manual promotion uses `oracle-instance-workload-mutation` | A future grant workflow must hold this group while grants/postchecks occur. Merely checking no run exists is insufficient. |
| `autopilot-runtime-role-hardening.yml` | Authorized owner command uses the same group | Same exclusion is needed for role changes. No hardening command is sent by this plan. |
| `oracle-light-0355-admin.yml` | Separate `oracle-light-backup-mutation` group | The future runner must also exclude this group during the same critical section. |
| `autopilot-paused-reconcile.yml` | Separate reconciliation group; admission/reconcile steps are currently `if: false` | Preserve those gates; its enabled diagnostics do not establish general operator exclusion. |
| `autopilot-chatgpt-role-callback.yml` | Per-comment concurrency group | Runtime/callback mutations are outside both groups. Inspect their actual capabilities and lock all affected admission/state tables or defer if quiescence cannot be maintained. |
| Neon SQL console, connected owner tools, local owner scripts, other chats/operators | Neither GitHub group | Requires explicit operator coordination covering the full window, or a separately reviewed access-control mechanism. A self-issued JSON flag is not proof. |

This map is deliberately incomplete: before apply, scan current workflows and
other credential consumers, including queued/waiting runs and old workflow
revisions, and resolve every source with relevant capabilities. Fresh GitHub
observations during preparation showed no queued run and only CI/code-scanning
work in progress; they do not reserve a future window.

### Selected design and exact critical section

Use one bounded coordinator for the future apply and immediate postchecks.
The intended implementation holds the main mutation concurrency group at
workflow level and the Light mutation group at the applying job level, both
without cancelling active runs. This two-group design is a proposal pending
implementation and validation; it does not cover every workflow or direct SQL.

1. Before opening the window, finish the production-specific apply/rollback
   runner and its fault tests. Inventory every relevant privileged entry point.
   Obtain the required operator freeze for direct owner channels and record
   its participants, exact database/branch, reviewed package SHA, start, expiry
   and scope in the protected operator record. An unaccounted writer means no
   apply. Do not infer agreement from an idle session snapshot.
2. Enter the reviewed coordinator's exclusions. Recheck main, run identity,
   target branch/database, active/waiting runs and live host HOLD. Preserve
   disabled native config and zero receipts/shared queue. The grant window
   does not stop/restart the service or release admission.
3. Capture the complete protected manifest using the owner connection, including
   the known production trigger and explained function versions. Check actual
   role closure, ACL grantors/options, owners and schema privileges. Do not
   silently apply 0372, normalize formatting or remove the extra trigger.
4. In a READ COMMITTED transaction, acquire bounded locks on the reviewed
   admission/state relations, re-read the manifest and apply exactly six direct
   grants. Recheck exact ACL delta and unchanged dependencies/config before
   COMMIT. Table locks cover conflicting row writes only, while operator
   exclusion covers privileged function/role/schema changes.
5. While the same maintenance exclusion is still held, verify through a new
   connection and perform fresh runtime metadata-only and host HOLD checks.
   Never call native RPCs to validate production permissions. If no task/receipt
   exists and the exact post-manifest still matches, the reviewed rollback may
   revoke only the newly added direct grants. Otherwise contain admission and
   reconcile; do not erase intervening changes.
6. Close the window only after retained evidence reports either verified
   committed grants or verified original-state restoration. Expired/lost
   coordination before COMMIT means ROLLBACK. Loss after COMMIT means preserve
   HOLD and inspect actual state; never blind-revoke or auto-resume.

No workflow is disabled, no session is terminated, no credential is rotated,
and no provider administrative role is changed by this plan. Those would be
separate material actions, not routine substitutes for missing coordination.

### Remaining implementation boundary

Catalog explanation and isolated cross-commit rollback are complete for the
tested cases. The production runner, two-group orchestration, complete writer
coverage, current host attestation, and protected operator freeze are not yet
implemented/established. Existing-receipt, wrong-context and interruption
faults must be covered by that exact runner. None of the rehearsal code is to
be pointed at production. The one-item provider loader and bounded pilot remain
separate from this grant-only maintenance window.


### Coverage correction after #1968 — 2026-09-26

The full workflow sweep at main `9fe64089ae745399754a5e43a406e20ff08a0c37`
found 320 workflows and 167 literal infrastructure/database credential-name
candidates. These are unreviewed capabilities, not proven production writers.
See [NATIVE_PERMISSION_WRITER_INVENTORY.md](NATIVE_PERMISSION_WRITER_INVENTORY.md)
and its immutable-Git inventory tool. The two-group design above remains a
partial proposal, not sufficient maintenance exclusion; resolve the additional
groups, ungrouped paths and external channels before wiring production apply.
The transaction engine and Neon binding are implemented in #1967/#1968; their
default maintenance guard still refuses every change.
