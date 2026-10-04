# Unattended readiness correction — cancelled candidate

Stacked on reviewed dormant adapter632aa9c59d5a554f44ea3c8c882db82fa1000860.
Main remainsf513afc4a1a4cae325b7baeeef5f4848dd29a7a5; IBM-owned workflows unchanged.

The Oct5 00:00–00:05 UTC candidate is CANCELLED because required installations
cannot be completed within authorized source/draft/isolated-CI scope. Its binding
readiness cutoff remains Oct4 23:55 UTC. No extension, rollover or replacement
window is inferred. candidate_readiness is executable and emits that cancellation
both before and after cutoff; it has no proof-JSON override or live GO switch.

BoundedController's actual successful real-SQL/teacher result now explicitly says
pilot_24h_admission=false and reports unconditional_revoke_at=stage_until.
The original immutable database expiry stays24h. The acceptance watchdog still
ends this experiment within15minutes; extending its wait would not establish an
independent installed24-hour recovery service.

## Concrete source preparation

github_channels.GitHubREST is a fixed authenticated HTTPS transport consuming the
existing GH_TOKEN in place. It has no arbitrary target, proxy, redirects, retries,
PATCH or DELETE. Requests and original records are bounded; credential/header/error
body contents are never returned or logged. OwnerObservation verifies exact
manual/main/owner/first-attempt run, source/workflow, completed owner inventory job
and step, full job-list completeness, and current main before/after. Its inventory
provenance explicitly cannot admit writes, recovery, or a24-hour watcher.

CreateOnlyClaims is a concrete dormant GitHub REST backend for nonpersonal public
C/P/N/D/A/R/contract bindings. Its root commit contains only claim.json, no inherited
repository/workflow or private catalog. Claim content blob/tree hashes and commit
identity/content are checked; exact authenticated references are read back.
Unique intent AND unique validation-build references must both be created.
A partial reservation permanently consumes its key and refuses admission.
Stages require complete identical reservation readback before any POST and create
their own one-time key. Repeated/conflicting/uncertain calls fail without update,
delete, repair or automatic replay. Current main is checked before/after claims.
It does NOT dispatch a build or SQL and is NOT wired into a live controller.
Creating refs/canon-claims/* on GitHub, namespace support, token privileges and
all repository event side effects still require operational qualification; loopback
tests prove the protocol, not live namespace installation.

source_closure inventories repository imports transitively, including imports inside
functions, package initializers, static __import__, explicit dynamic import sites,
public resources and SQL files. Artifact includes exact checkout commit/tree and
file hashes. Status remains SOURCE_REVIEW_REQUIRED: neither a manifest nor a Git
tree hash proves human/I2 review, trusted imports or third-party dependency safety.
Schema runtime functions/triggers and dynamically resolved resources remain review
obligations.

## Exact installation blockers and minimal changes not applied

1. Current owner job permissions are contents:read. Creating Git references requires
   contents:write. This is repository-wide access expansion, not a namespace-scoped
   permission; do not enable it merely to run this backend.
2. Automated workflow dispatch requires actions:write. No installed stage command
   exists in the IBM-owned owner workflow; it is not changed here.
3. Authenticated interactive Vercel MCP requestId-filtered logs are available, but no
   authorized unattended runner credential/session has been qualified. Do not copy
   the connector credential or infer runtime correlation from build events.
   The documented runtime REST stream does not provide the required requestId
   contract; no guessed endpoint or permissive fallback is implemented.
4. No separately supervised managed-credential canon recovery installation supporting
   original24h expiry and surviving controller/GitHub cancellation is qualified.
   Existing IBM/Oracle/HOLD journals and unrelated Actions watchdogs cannot be
   repurposed under this authorization. A new host/service/policy is an operational
   change requiring review; no unproven exact provisioning command is proposed.
5. Full source/dependency review and main continuity through the entire recovery
   period remain open. Emergency-only reviewed source transitions do not authorize
   arbitrary normal stages or an automatic main-source bypass.
6. Final admission must freshly qualify PostgreSQL17+ transaction_timeout and the
   actual current application LOGIN identity/denials. Earlier PG18 fixtures and
   owner inventory do not establish these mutable production facts.

Primary permission references:
https://docs.github.com/en/rest/git/refs#create-a-reference
https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
Primary runtime-channel references:
https://vercel.com/docs/rest-api/logs/get-logs-for-a-deployment
https://vercel.com/docs/agent-resources/vercel-mcp/tools

## Qualification scope

Secret-free <=10-minute Actions: anonymous localhost HTTP protocol server,
atomic concurrent reservations, conflicts, partial claims, lost post-commit response,
forbidden paths/methods, wrong owner/source/event/attempt, cancellation before/at/
after cutoff and no rollover. Original real PostgreSQL18/teacher-route tests and
L1/API regressions run again. The closure and cancellation are retained as artifacts.
No actual GitHub claims, Vercel observation, production credential, build, deployment,
SQL, permission change, activation, main merge or pilot GO is performed.
