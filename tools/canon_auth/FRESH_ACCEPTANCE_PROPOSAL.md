# Fresh bounded acceptance proposal — separate from the expired validator

Status: REVIEW PROPOSAL ONLY. No live dispatch, build, SQL write or main promotion.
Observed main: f513afc4a1a4cae325b7baeeef5f4848dd29a7a5.
Offline pilot candidate: PR2108, 5c5dd778a2c6b40b899981378b911f89845d76ff.
Exact offline CI37237599112: 361 tests plus PostgreSQL18 PASS; I2 PASS.
The former 2026-10-04T18:00:00Z validator remains expired and unchanged.
This is a new finite operational attempt for the already approved two-rule pilot,
not renewal of an old expiry or a request to repeat the same pilot authorization.

## Proposed finite UTC window

- All source installation, independent review, READY evidence, receipt and
  recovery rehearsals must be complete by 2026-10-04T23:55:00Z.
- One fresh validation build may start at or after 2026-10-05T00:00:00Z and
  strictly before 2026-10-05T00:05:00Z.
- No baseline/initial/revoke/reactivate transaction may start at or after
  2026-10-05T00:15:00Z. The controller stops earlier if the 420-second build
  budget or an individual phase deadline expires.
- Recovery is a distinct owned-only operation, not a normal stage. An admission
  deadline must never prevent removing owned active bindings. Recovery remains
  available during the original pilot lifetime and afterward if persisted
  owned bindings still require revocation. It cannot import, activate or renew.
- If source/install evidence is not ready, this proposal is not launched.
  No automatic timestamp rollover, delayed retry, marker redeploy or second
  validation build. A later operational proposal gets its own exact finite
  timestamps and source review without resetting any existing pilot expiry.

The pilot TTL is separate: first successful initial activation records server
now()+24h as original_expiry; every reactivation reuses it. Before any fresh
initial activation, prove the deterministic source/position/run/rule IDs absent.
If a previous pilot has already started, do not create a replacement namespace,
new run or new expiry. Only its existing approved stages or recovery can apply.

## Use existing app channels; do not duplicate identity qualification

At f513, bridge_school_api/main.py healthz executes:
current_user plus count(public.school WHERE stable_name=EXPECTED_SCHOOL).
It returns status=ok only if current_user is the expected app principal and the
school count is exactly one. The already observed f513 status=ok therefore has
real DB/effective-principal/school evidence. It does not prove teacher SQL gate
behavior, session_user, independently checked immutable server tags or a
certificate-verification mode. Its short CDN cache is also relevant when
interpreting a specific request; do not invent a fresh SQL execution per GET.

The existing authenticated route
/v1/knowledge/validation/runtime-identity already supplies the narrower missing
configuration/target evidence if a refresh is needed. It calls the existing
incident probe to reject unexpected effective URI parameters, unknown raw source
hosts and libpq environment overrides; expected direct-to-pooler canonicalization
is permitted; uses the same resident app connector;
checks TLS, host/port, current principal/database and immutable server tags;
runs bounded read-only SQL and explicitly rolls back. Its existing policy permits
sslmode=require as well as verify-full: it proves TLS-in-use, not a universal
certificate-verification policy. Verified owner TLS remains a separate receipt.
It reports
OBSERVED_NOT_ADMITTED, not teacher or canon readiness. Consume it once in the
same fresh in-place caller; do not create a second identity build or export an
API token. Previous SHA711 evidence remains historical, not a current receipt.

There is no existing public route that directly exercises the tournament school
catalog before a position exists. Missing-position teacher 404 proves the
position SELECT and request/auth fence, but returns before catalog retrieval.
Knowledge query and L1 runtime routes are different boundaries and must not be
presented as this catalog test.

The minimum remaining behavior call is therefore the already reviewed
tools.canon_auth.teacher_behavior.inspect_teacher_connection from PR2108,
called IN PLACE by the one fresh validation build using bridge_school_api.db.connect.
No new app route or generic identity adapter is necessary. Before that call:
- fresh admission and code/target pins pass before any credential lookup;
- incident_db_probe.probe(False) must pass the existing strict configuration
  checks (including permitted expected direct-to-pooler canonicalization); actual
  connection host/port/TLS must match the existing app target;
- the connection is dedicated and idle, session_user=current_user=app principal,
  current database is the expected database;
- helper pins pg_catalog, READ ONLY, bounded timeouts and forced rollback;
- synthetic nil school absence is checked by boolean EXISTS;
- internal gate EXECUTE metadata must be false, actual attempted invocation
  must fail with InsufficientPrivilege inside a rollback savepoint;
- the permitted SECURITY DEFINER school catalog actually executes and a boolean
  EXISTS must be false for the absent synthetic school.
Only fixed booleans escape this call. No application rows, DSN or credential
values. If privileges differ, stop; do not GRANT or downgrade the requirement.
Review exact deployed SQL routine definitions/dependencies first: READ ONLY
alone does not exclude external side effects in an unreviewed function.

This is live behavior only when the actual fresh managed build performs it.
Offline app-login qualification and the HTTP fixture with owner SET ROLE are
separate evidence; neither substitutes for that live call.

## Exact source/deployment pins after installation

No hypothetical installed SHA is filled in now. Seal these fields only after
reviewed source installation and ordinary READY deployment:
- S: exact installed source SHA and Git tree; final CI/I2 and module hashes.
- D: exact READY production deployment ID, git SHA=S and official origin.
- OBS: authenticated alias/deployment observation, no older than 300 seconds.
- N: one new immutable intent ID, fixed timestamps above, no attempted build.
- B: the one acceptance-control commit/build. It may be an empty commit on main
  with the exact fresh marker/context and the SAME Git tree as S. After creation
  its exact SHA is recorded externally; never embed its own SHA in its code.
- A: the one immutable Vercel validation-build ID for B; record it externally.
- C: original compiled-plan code SHA, fixed once to S (not B or later R).
- P: exact compiled pilot-plan hash using that C and approved compiler/package/schema
  hashes, determined without publishing the school ID or private registry.
  The retained intent ledger binds N/P/C. Later stages and recovery recompute
  with the original C and deterministic IDs; R must never replace C.
- R: exact externally reviewed current main SHA for each owner stage/recovery
  dispatch, plus the unchanged reviewed operator/module hashes.

Stage execution requires B=current frozen main during normal acceptance and
exact installed source equality. READY target stays D/S while validation B
always exits nonzero and never promotes. This intentional validation-only
relationship requires full tree equality S/B and before/after alias binding;
it is not permission to accept an arbitrary stale deployment.

A new fresh build dispatcher/entrypoint is required for this finite proposal.
It selects only the new sealed fresh marker; the legacy validator remains
separate and fails its original deadline. Ordinary builds stay inactive.
Public context is allowlisted; the entire environment is never copied.
New minimal bundle allowance is only for that fresh entrypoint and the already
reviewed teacher_behavior/resident_preflight helpers. No SQL compiler, owner
credential code, private registry, tests or generic operator enters the app bundle.
This source must be implemented/reviewed before S is installed; this document
does not pretend that the currently installed legacy hook is already usable.

## One app build, existing managed credentials

Existing BRIDGE_API_TOKEN is used only in memory against the official origin.
Existing BRIDGE_APP_DATABASE_URL stays inside the app connector. Neither is
copied to GitHub, exported, logged or read by this agent. Missing credential or
unsupported invocation stops the attempt; no new credential or env/policy change.

Validate marker, window, source tree, production/main context, exact S/D/N and
observation freshness BEFORE token or DB credential lookup. One build ID A,
one attempt; no redeploy/rerun. Coordinator records A durably before pilot writes.
The ephemeral claim alone is not a durable single-attempt guarantee.

Use the existing git-connected production build hook to invoke the new fresh
entrypoint, after source installation. Current tools expose deployment/log
reads, not a callable create-deployment action; do not claim a nonexistent tool,
use a share-link bypass or repurpose the old expired marked commit. Creating
the new exact control commit is a later coordinated source mutation, not done here.

Maximum40 HTTP requests, 8-second socket timeout, 420-second process budget,
redirect/proxy refusal, fixed paths, unique request IDs and strict no-write
assessment contracts. At most four preflight requests:
healthz, authenticated overview, runtime-identity refresh if needed, and the
fixed synthetic missing-position teacher POST. The optional identity refresh
occupies the one spare request in the existing maximum39 lifecycle budget.
No additional route probe or broad application-data read. Overview data stays
in memory; only fixed status/receipt and school-binding equality booleans emit.

## Existing owner runtime: fixed stages and independent recovery

Reuse the qualified protected main-only owner contour and its existing managed
owner secret. The workflow file belongs jointly to parent coordination and the
IBM draft; do not edit or merge it independently. Existing maintenance and IBM
jobs must not run for canon stage/recovery scopes. No new grants, credentials,
environment policy, actor/ref bypass or production principal.

The installed canon-owner-probe is inventory ONLY: do not repurpose its current
command as arbitrary SQL. A fixed stage/recovery entrypoint is the necessary
remaining owner implementation. It accepts only baseline/initial/revoke/
reactivate/emergency, never SQL text, arbitrary tables, school input or namespace.
Resolve the target using the existing EXPECTED_SCHOOL constant in managed code;
require exactly one active school and compare it in memory with the app binding.
Do not publish that school's ID. Recompute the existing deterministic fixed plan.
Preserve namespace, position ID, two payload hashes/TDEC and compiled row budget.

Normal stages retain repo/main/owner+trigger-owner/manual/exact-SHA guards,
qualified target/transport and live-main before/after checks. Each dispatch is
first attempt, fixed intent N and expected previous stage; no old-run rerun.
The supported owner UI dispatch channel is the one already proven by run
37235562804. There is no callable workflow_dispatch tool in this toolset.

Before each stage the coordinator retains original authenticated Vercel/GitHub
observations, verifies the previous correlated phase and submits its bounded
allowlisted receipt/hash with the owner stage instruction. Normalized JSON alone
is not authentic control-plane evidence: it is an owner-coordinator attestation
bound to retained tool records. The workflow must not pretend it independently
queried Vercel without a resident control-plane credential. No new Vercel key is
proposed. Persist sanitized stage/readback receipts as managed Actions artifacts;
original private observations remain in their existing authorized contour.

Recovery has its own reachable main-only job, independent of the app build,
normal-stage window and alias success. Before writes prove capabilities for
both revoke tables; then execute only the original owned emergency plan, append
at most its two existing audit rows, and read back owned status/valid_to.
No active owned rows => do not invent them to make recovery succeed.
If baseline never committed, verify absence and emit a no-op receipt.
If baseline exists, terminal emergency audit prevents later activation.
Repeated emergency stays idempotent and never resets original_expiry.

Normal main is frozen through acceptance. Later main promotion must retain the
reviewed recovery module/compiler until the pilot is closed; a recovery dispatch
still uses actual main with its fresh externally reviewed SHA R, while the
original pilot approval/IDs/code SHA remain unchanged. Do not bypass the main
gate with an old feature ref when main changes. Inability to establish that
reviewed current recovery channel is a blocker BEFORE initial activation.

A process finally block is not guaranteed recovery. Lost connection/process or
unreachable owner runtime reports emergency_revoke_unproven; TTL is bounded
eligibility, not a persisted-revoke receipt. No live pilot begins until an
independent owner recovery dispatch has been verified reachable and its exact
fixed entrypoint/fault recovery tested in disposable PostgreSQL.

## Per-phase receipt sequence and final acceptance

| Step | Required proof before next transaction |
| --- | --- |
| Preflight | app behavior booleans; health/auth/identity as needed; missing position typed404; S/D/B/A/N and recovery bound |
| Baseline (2 rows) | teacher ABSTAIN, empty eligible binding, request correlated to D/S |
| Initial (34 cumulative) | 3H SUPPORTED and 3S CONTRADICTED, exact payload/source/TDEC/explanation, shape-only result |
| Owned revoke (35 cumulative) | teacher ABSTAIN and owned activation readback revoked |
| Reactivate (40 cumulative) | both assessments restored; original expiry equal; no duplicate eligibility |
| Failure at any point | stop progression; independent owned recovery; database readback; API ABSTAIN if available |
| Final | all waiting polls and phase receipts correlated; rows40/max42, history retained, output/search/final-decision writes0 |

For EVERY HTTP request, call existing get_runtime_logs with required teamId,
projectId, deploymentId=D, requestId=the exact receipt, bounded since/until and limit.
Require exactly one authoritative request/invocation match with path/status/time/
deployment. Multiple console/log lines belonging to that same invocation are not
multiple requests: preserve them and normalize only when their common invocation
identity and metadata are proven. Do not silently collapse contradictory records.
Full-text query or aggregate counts are not a request match. Keep actual tool
query parameters and original responses alongside normalized checks.
Before/after get_deployment and alias observations must still bind D/S/origin.
Before any pilot write, prove these filtered request/invocation records are
actually available, including the credential-free health receipt. A cached health
GET can lack a fresh function invocation; body=ok alone cannot fill a missing log.
No-match, multiple distinct invocation matches, stale alias, unavailable logs or a wrong status stops
the next write. Correlate waiting polls too; HTTP results stay pending until this.
App-build behavior receipt is separately bound to A/B and tree S, not falsely
labelled as a teacher HTTP request receipt.

Healthy completion leaves only the already approved original24h pilot eligible.
Failure recovery proves persisted owned revoke; missing HTTP evidence is reported
separately rather than claiming a recovered teacher result. Points/HCP/priority,
questions13/14, other tournament rules and other consumers remain out of scope.

## Remaining implementation and material-scope boundary

1. Finish source/I2/CI review of PR2108 entrypoint fix and both coordinated drafts.
2. Implement only the fresh finite build dispatcher plus in-place helper call,
   fixed owner stages/recovery and bounded receipt persistence/validation above;
   fault-test exact final source offline. Preserve legacy/maintenance/IBM gates.
3. Install reviewed source only after parent coordination; observe S/D; seal the
   exact public pins and finite intent; prove original attempt absence/recovery.
4. Perform the one bounded acceptance attempt only after that final review.

No new access is needed in this design. If existing app credentials are not
available in the managed build, source/log channels cannot retain authentic
receipts, or the existing owner UI/runtime cannot provide independent recovery,
report that exact blocker. Do not silently add keys, policy, routes, role grants,
jobs for students or replay/HOLD changes. Such a material expansion would be a
separate decision; this proposal does not request or authorize it.
