# Oracle Light native transport activation proposal

Status: ASSURED / PREPARATION ONLY. Production HOLD remains mandatory.
Baseline: f678d3e58f23a4893b65deb98b705b83e67bb28d, 2026-09-25.
Tracking: #1946. This proposal grants no permissions and activates no worker.

## Confirmed evidence

- Owner SSH service-sandbox diagnostic: CLI 0.157.0, CLI_AUTH_READY, full
  HOLD/READ_ONLY_PASS/queue0 before and after, unchanged service invocation.
  Evidence: https://github.com/olegmed1-art/bridge-video-free/issues/1946#issuecomment-5837947543
- Live release is 5eb0e1bb2c2932bd8d02ff187b9cf24f6bc09c7c. Its bridge accepts
  ubuntu/service only; the light profile exists in main but is not deployed.
  Live worker.py and worker_v17.py have no direct native transport imports.
  Main worker also has no native transport wiring.
  Evidence: https://github.com/olegmed1-art/bridge-video-free/issues/1946#issuecomment-5838003011
- Service role autopilot_light_worker_login has effective EXECUTE=false on all
  seven observed native_cli functions. Both native tables exist but SELECT=false.
  The enabled value is UNKNOWN, not confirmed false. No native RPC was invoked.
  Evidence: https://github.com/olegmed1-art/bridge-video-free/issues/1946#issuecomment-5838074483
- Installed CLI help declares `cloud exec --env <ENV_ID> [QUERY]`. The bridge
  hardcodes `bridge-video-free` and passes `-` as QUERY with a prompt on stdin.
  Tagged upstream source confirms that `-` consumes stdin, a unique environment
  label resolves to an ID, and successful creation prints a task URL. Thus the
  invocation format is supported; actual access to the intended environment
  and its repository binding remain unverified. Source:
  https://github.com/openai/codex/blob/rust-v0.157.0/codex-rs/cloud-tasks/src/lib.rs
  (`run_exec_command`, `resolve_environment_id`, `resolve_query_input`).
- Tagged upstream `run_status_command` exits 1 for every non-READY status and
  `task_status_label` emits ERROR. Current bridge returns PROVIDER_STATUS_UNKNOWN
  immediately for nonzero exit and does not recognize ERROR. Consequently a
  provider error cannot reach its existing terminal-failure handling. Correct
  this contract with representative CLI output/exit fixtures before activation;
  do not turn arbitrary nonzero exit or network errors into terminal evidence.
- Local baseline tests: 49 passed across bridge, delivery, queue. These use
  substitutes for external dependencies, not a real PostgreSQL receipt lifecycle
  rehearsal, service API check, or cloud task run.

## Minimum proposed database interface

The runtime needs schema USAGE (verify existing effective privilege first) and
EXECUTE on these exact signatures only:

| Queue operation | Function |
| --- | --- |
| Reserve | autopilot.native_cli_reserve(uuid,jsonb,text) |
| Snapshot | autopilot.native_cli_snapshot(uuid) |
| Current authority | autopilot.native_cli_current(jsonb) |
| One-shot intent | autopilot.native_cli_begin(jsonb) |
| Acknowledge | autopilot.native_cli_ack(jsonb,text,text) |
| Finish | autopilot.native_cli_finish(jsonb,text,jsonb) |

Do not grant native_cli_authority_locked(uuid,jsonb), native table SELECT/DML,
PUBLIC privileges, membership in owner roles, or default privileges. The private
helper remains callable only within its existing SECURITY DEFINER chain.

This list is a proposal, not executable grant SQL. Before writing an apply
procedure, inspect actual definitions, owners, security-definer/search_path,
ACLs, schema USAGE, role membership and migration dependencies using an
already authorized database owner channel. Record expected definition hashes
and original ACLs. Existing table/function names alone do not prove migration
integrity. Do not borrow owner credentials or widen the service role for audit.
The current Neon connector rejects calls before execution because project_id
is required by its backend but absent from its exposed schema.

## Integration design to implement and review

1. Add an explicit dormant native transport entrypoint/adapter with mandatory
   light profile and no default-to-ubuntu fallback. Keep existing production
   worker entrypoint unchanged until a separate exact release deployment.
2. Require primary-source repository/PR/head/branch authority and a verified
   Codex Cloud environment identifier/repository binding. The tagged CLI source
   supports current prompt transport and create-response format; verify remaining
   status/diff parsing contracts before submitting anything.
3. Compose NativeQueue, NativeAuthority and delivery.advance. Use the same
   fixed service DB identity for reservation, intent, ACK and finish because
   receipts are owned by SESSION_USER. Never switch to an owner connection.
4. Operate on one explicitly selected existing shared-admission reservation;
   do not add a second scheduler, claim arbitrary work, or bypass capacity.
   READ_ONLY/VERIFY only; REPAIR/publication stays outside this pilot.
5. Under HOLD, forbid all reservation/intent/ACK/finish/provider calls. A local
   health check is separate from task delivery. Preserve fail-closed handling
   of unknown creation outcomes; never resubmit to another account or host.
6. Model pilot admission separately from normal continuous processing. Opening
   the whole worker queue to ACTIVE is not an acceptable substitute for a
   one-item pilot. Produce exact admission/config changes for review.

READ_ONLY refers to the task's repository behavior. The receipt lifecycle still
writes queue bookkeeping. The present database-read-only HOLD connection cannot
complete that lifecycle; changing that boundary requires an explicit reviewed
activation decision, not simply granting EXECUTE.

## Isolated rehearsal required before production grants

Use a disposable local PostgreSQL fixture or separately authorized isolated
branch, never production task rows. Rehearse actual SQL with the intended
runtime identity and a deterministic fake provider:

- exact six-function interface succeeds; private helper and direct tables deny;
- disabled configuration and wrong owner/head/assignment reject reservations;
- duplicate reserve and lost ACK preserve one provider ID;
- lost create response stays quarantined; no second submission;
- stale authority cannot report success; terminal replay must be identical;
- paused work does not spawn followups; native slots release only on terminal;
- HOLD prevents side effects and one-item limit prevents additional admission;
- rollback restores only recorded grants/configuration and the old release.

Fake-provider rehearsal does not establish real cloud access. The first real
cloud task, even READ_ONLY, requires the separate bounded-pilot decision.

## Promotion and rollback order

Before promotion: independent I2 review of exact code/SQL/release, fresh main,
full live HOLD/queue0 attestation, no existing native inflight receipts confirmed
by the authorized owner, and an exact recorded rollback manifest.

Stage immutable release and verify its imports/profile under service isolation.
Review and apply only recorded missing runtime permissions; keep admission HOLD
and native enablement unchanged. Post-check exact ACL delta and full attestation.
Any restart/release switch requires its own reviewed pre/post checks; prior
same-invocation attestor cannot be represented as proof of a successful restart.

Before any task exists, rollback can revoke only newly added direct grants,
restore the recorded enablement value and select the prior immutable release.
Do not blindly REVOKE existing/inherited access or overwrite preexisting ACLs.
After creation may have occurred, retain receipts and local journal, stop new
admission and reconcile the original provider ID. Do not drop receipt tables,
reset creation intent, erase credentials, or release a slot on uncertainty.

## Next concrete gate

Verify live cloud environment binding and obtain authorized read-only
owner metadata/config inventory. Implement and rehearse the dormant adapter and
exact permission delta, then request the separate bounded-pilot launch decision.
No new owner SSH command or production mutation is warranted by this proposal.
