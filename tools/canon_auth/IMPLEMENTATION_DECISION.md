# One bounded implementation decision: no operational admission

Reviewed baseline12fd5acb1fd1448e958b421ecb89031380654758 (draftPR2113).
That immutable review head is preserved. The cancelled midnight candidate stays
cancelled; this decision supplies no date/window, reschedule, installation or GO.

## Immediate source-review correction

SQL review read all148 migration bodies and their source identities. The actual
knowledge_version_source primary key is(version,source,relation_type), while12fd
inventories only(version,source). A sibling relation can escape its extra-row
check. The correction inventories all three columns and the exact original
derived_from default, with text-aware key predicates. Original compiler SQL/P,
deterministic IDs,42declared rows, semantic sources and expiry are unchanged.
Actual PostgreSQL counterfactual executes immutable12fd inventory on an added
supports sibling; new inventory/stage/recovery refuse and preserve all rows/
activation statuses. Both3H/3S links and changed-key cases are covered.

## Recommended finite path and exact permission targets

Retain the existing fixed adapter; add no general orchestrator or privileged
SQL interface. Future installation uses separate narrowly coded jobs. The names
below are exact proposed targets, not existing/installable grants:

| Proposed workflow / job | GitHub token permissions | Credential boundary |
|---|---|---|
| .github/workflows/canon-claim-publish.yml / publish | contents:write ONLY if the reviewed Git-ref backend is selected | no DB credential; no production environment |
| .github/workflows/canon-stage-dispatch.yml / dispatch | contents:read,actions:write ONLY if separate automated jobs are selected | no DB credential or claim write |
| .github/workflows/canon-observe.yml / observe | contents:read,actions:read | no DB credential or claim write; original run/job records |
| .github/workflows/canon-owner-stages.yml / owner-stage | contents:read | existing database-production owner binding in place only; no contents/actions write |
| .github/workflows/canon-owner-stages.yml / inspect | contents:read | same managed binding, read-only SQL |

Explicitly declare all other permissions none. workflows:write is unnecessary.
Source checkout/current-main GET need contents:read. Authenticated run/job endpoints
need actions:read; public anonymous visibility is not authentication/permission
qualification. Prove each proposed token endpoint with harmless reads after
installation and before any write. No token/secret values are read, copied or
published by the agent. These workflows/jobs do not exist in this change.

contents:write and actions:write are backend choices, not inherent requirements.
One manual owner/controller job executing local fixed stages would avoid automated
dispatch and actions:write, but still needs real unattended observation and
independent recovery. Existing owner_stage pins the IBM owner workflow and requires
manual owner actor; automated GITHUB_TOKEN dispatch would use a different actor.
Therefore a separate reviewed identity/context binding is a required code change,
not something actions:write alone solves. Do not edit the IBM workflow or silently
relax the existing actor/ref/attempt guards.

## Existing durable storage comparison

Existing OCI bucket bridge-light-autopilot-backups has a real conditional adapter:
private-object checks, create-only archives, If-None-Match/If-Match ETag heads,
bounded readback, no retries/delete, approved budget. It is technically a better
way to avoid GitHub contents:write IF a separate canon-only prefix/registration,
retention and scope are authorized and independently qualified using its existing
managed credential in place. Current adapter is fixed to native-journal/checkpoint-v1
and recovery-assets-v1; its snapshots/accepted heads bind native permission/HOLD
operations. Reusing that prefix or its trusted accepted heads for canon is forbidden.
No canon namespace is approved/qualified, and no existing fresh operation registration
or deletion detection for canon has been demonstrated. This is a purpose/assembly
approval and code/qualification gap, not proof that new OCI IAM is necessary.

The local root-owned /var/lib/bridge-native-maintenance store survives runner loss
but is not an off-host authoritative claim store; its docs explicitly do not prove
instance/volume-loss survival or approve operation scopes. Ephemeral runner files,
artifacts and concurrency groups do not provide atomic create-only global claims.

Existing GitHub broker already has bounded contents:write internally for draft
repairs/mailbox actions, but its physically enforced policy permits only those
branches/files, forbids /actions, and never exports its credential. Adding canon
claims/dispatch is a separate broker policy/admission change; the existing App
capability does not authorize repurposing or token extraction. Prefer qualifying
the OCI canon prefix before recommending repository-wide token expansion. Until
that comparison is admitted, Git-ref publisher remains dormant and no grants are
recommended as an unconditional prerequisite.

## Concrete24-hour recovery / Vercel channel on existing infrastructure

Only existing Oracle Light host/PID1/volume, existing managed owner DB binding,
existing OCI bucket and existing Vercel/GitHub integrations may be used; no new
instance, subscription, credential, database role or broker is proposed.

A separately named canon unit/timer on existing Light would own a private durable
contract/intent/readback and original expiry, not the GitHub runner/SSH stdio.
It must be armed before baseline, verify exact source/plan/ownership, survive
controller and runner cancellation, revoke on acceptance failure/uncertainty and
unconditionally at original immutable24-hour expiry. A failure-safe independent
timer must invoke fresh readback/revoke even after watcher restart/process death.
No disarm cancels a committed/uncertain obligation; revoke must be confirmed before
removing the unit. No24-hour wait is implemented by extending the acceptance
watchdog: its current15-minute unconditional revoke remains unchanged.

Existing native PID1 transports accept only3/100/140seconds, use --wait --pipe,
Restart=no, and launcher-finally cleanup. They are not this persistent service.
Existing owner-host read probe receives its managed credential transiently through
SSH stdin and ends in100seconds; it is not proof of an in-place24-hour owner
capability. Current host worker/HOLD access cannot be assumed to revoke canon.
Thus installation approval must name an existing in-place managed recovery
capability/endpoint, or approve the needed capability integration explicitly.
Do not copy a DB credential to the host or invent a daemon privilege. Current
evidence does not establish such an endpoint; production admission stays blocked.

If recovery is dispatched back to the existing managed GitHub DB environment,
host watcher may use only an existing opaque authorized broker channel; its present
broker policy forbids /actions. A separately reviewed fixed emergency-only route
and App Actions capability would be needed, with actor/source/attempt continuity.
That alternative survives cancellation of the normal runner, but cannot promise
recovery during GitHub/broker outage and has not been installed/qualified.
No broad workflow ID/ref/input forwarding is acceptable. No secret export is allowed.

For Vercel use an existing authorized unattended CLI/OAuth context at the observer:
vercel logs --deployment D --request-id RID --since UTC --until UTC --json,
with explicit project/team scope, bounded count/time and privately retained
original records. Verify READY exactD/S before and after, one original invocation
per unique responseRID/path/status, preserve every poll, reverify before acceptance.
The official CLI supports exact request-id filtering; documented build events do
not serve runtime correlation. Current interactive MCP OAuth access is only proof
of interactive reads. No unattended in-place session was found in installed
workflows; protection-bypass/OIDC access to deployed brokers is not control-plane
log authorization. Without qualifying an EXISTING managed session, no channel
installation can be admitted under the no-new-credentials constraint.

## Dependency review and admission evidence

Conservative tracing found75Python modules plus148SQL files; the owner import graph
includes FastAPI/Pydantic/db and DDS/routers merely to reuse application constants.
The actual deployed closure must include app.py (missing from the previous inventory).
Known safe narrowing: read the literal EXPECTED_SCHOOL without executing main,
reuse the strict stdlib connection_parameters parser while preserving every
existing owner libpq override, and consume only pure compiler/profile data without
importing the HTTP teacher router. This is a required narrow refactor, not proof
that removing one import makes the owner runtime psycopg-only.

Existing managed driver pins hashed ARM64 CPython3.12 psycopg/binary3.3.4 and
typing_extensions4.15.0; acquire its verification lock before any driver import.
Broad CI used typing_extensions4.16.0, FastAPI0.141.1, Starlette1.7.0 and
Pydantic2.13.5/core2.46.5. Compatible version ranges do not qualify the exact host
bundle, and the driver does not supply FastAPI. Installed schema function bodies,
trigger/constraint/RLS bindings, owners/searchpaths, effective app ai-schema and
position SELECT access, PG17+ transaction_timeout, current application LOGIN and
current recovery capability require fresh read-only qualification before GO.
148SQL bodies were reviewed; installed production definitions were not inferred.

## Rollback and exact missing approval

Before activation: remove proposed job-level write permissions/routes and disable
new canon entrypoints; existing owner/IBM/HOLD services remain untouched. Do not
delete consumed claims, journals, accepted heads or assets. A source revert does
not reconcile a committed/uncertain SQL intent.

After any possible mutation: preserve artifacts/state, independently inspect and
perform ONLY the owned emergency revoke using pinned reviewed source. Keep the
recovery capability/unit and main continuity until persisted active0 is confirmed,
then retire canon-only service/job access. Foreign/partial ownership is UNPROVEN,
not overwritten. Claims are never replayed/repaired/deleted for another attempt.

Missing approval is for ONE chosen claim backend and ONE named in-place recovery/
observer assembly, with exact canon scope, runtime source and managed capability;
it is not blanket authorization for both GitHub writes plus OCI/DB changes.
No proposed permission, policy, watcher, credential or production action is applied.

Primary references:
https://docs.github.com/en/rest/git/refs#create-a-reference
https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
https://docs.github.com/en/rest/actions/workflow-runs#get-a-workflow-run
https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run
https://vercel.com/docs/cli/logs
