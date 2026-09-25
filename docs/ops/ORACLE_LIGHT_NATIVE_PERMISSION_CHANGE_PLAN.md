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
function definitions and database/session read-only settings unchanged.
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

The live connection remains read-only: EXECUTE grants do not permit receipt
writes. A READ_ONLY repository task still needs bookkeeping writes. Any change
to that database boundary belongs to a separate reviewed activation package.

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
