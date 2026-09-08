# AP-SERVER-WITNESS-V2-23 — partial draft, not approved for execution

Base inspected: e389b644556bc841739c0b2aa1022a03199ef817.
No repository writes, merge, workflow execution or server actions authorized.

Files are a review scaffold, NOT a completed operational workflow.
The workflow is unconditionally disabled and contains no transport or secrets.
The Python observer must not be run outside mocks during this preparation.

Important source finding: the current systemd unit mounts the secrets DIRECTORY
at /run/secrets. An old file inode is therefore not established by
container_recreated=false. Actual stat evidence is needed.

Implemented: allowlisted service/container metadata, CPU/memory/load/free space,
device+inode comparison through resident /proc root, bounded read-only SQL using
resident-mounted credential, before/after identity guard, secret-safe errors.

Still required before an executable proposal:
- prove actual worker DSN/configuration selection without exposing secrets;
- OCI lifecycle/shape read using already configured authority;
- complete active Actions and host work/conflict reconciliation;
- verify credential mount path against live mount metadata and worker identity;
- bind health thresholds, queue access and all observations to one bounded run;
- strengthen race detection and test partial failures at each collection stage;
- independent review of exact head, approved SSH transport and pinned identity.

A mounted-credential connection does NOT prove the running worker uses it.
The draft never returns READY_FOR_PRECANARY_DECISION or PRECANARY_READY=YES.
The mounted credential query is separate evidence, not resident_target.

Proposed future authorization scope (NOT execution authority): one no-retry,
max-five-minute diagnostic against the existing Oracle instance only, no
start/stop/restart/recreate, no installs on host, no credential edits, no DB DML,
no media/DDS/OCR/ASR, no Vercel changes, no raw secret/env/log output.
Only after missing observers and exact code review can a concrete Director GO
contract be presented. No lifecycle contract is justified by this draft.

Local tests: python -m unittest discover -s witness-draft -v (mocked transport).

## Revision 2 — fail-closed contract hardening

Fresh main recheck unchanged. Primary source: universal_video/video_queue.py,
database_url_from_env: direct BRIDGE_VIDEO_QUEUE_DATABASE_URL first, then
BRIDGE_VIDEO_QUEUE_DATABASE_URL_FILE, then BRIDGE_APP_DATABASE_URL, then
BRIDGE_WORKER_DATABASE_URL. spool_worker.py separately enables queue polling
using direct/file/worker variables (APP alone does not enable polling).

Added a pure, secret-redacting precedence classifier with synthetic tests. It
does not read /proc environments or claim effective in-memory worker state.
Added strict probe JSON validation: exact keys/types, duplicate-key rejection,
nonnegative integer counts, bounded size, and no unexpected payload output.
On partial failure or race, inode/probe evidence becomes UNKNOWN. A mismatch
requires completed stability checks before any RECREATE_REQUIRED conclusion.
The Python entry point is now also disabled, not just the workflow.

Remaining scope is unchanged: transport, OCI, full conflict inventory and
effective worker configuration proof are NOT implemented. This package must
not be represented as operationally ready or as live server evidence.

## Revision 3 — bounded evidence validators

evidence_contract.py validates OCI CLI JSON with exact instance identity and
finite positive CPU/RAM, and complete active Actions response sets. Counter/list
disagreement, truncation, duplicate runs or changed run statuses fail UNKNOWN.
Known heavy process comm names can prove BUSY; their absence cannot prove IDLE.
These are pure validators, not collectors and not live runtime evidence.

Preparation remains incomplete. Do not repeatedly treat passing more synthetic
tests as closure of AP-SERVER-WITNESS-V2-23. The next integration gate is one
reviewed read-only transport plus complete resident observation contract.

## Revision 4 — disabled transport integration

runner.py now wires fixed GET-only GitHub/OCI reads and fixed pinned-host SSH
probe to before/after checks. It requires preconfigured CLI access, an explicit
SSH key and a one-key known_hosts file with the existing expected fingerprint.
It does not search for credentials, install software or change host files.
Both the CLI and workflow remain disabled. No server call was made in tests.

Historical revision-4 findings: self-run exclusion, timeout hierarchy and
receipt schema were incomplete. See revision 5 below for local corrections.

This is a local code-preparation checkpoint, not completion of server witness.

## Revision 5 — self-run identity and strict receipt integration

Implemented exact self-run exclusion using a fresh GET run detail: numeric ID,
exact SHA, first attempt, workflow_dispatch, fixed intended workflow path,
repository/head repository, owner actor/triggering actor and in_progress status.
The active-run list must contain exactly one matching record with the same
identity; another run, missing self or attempt/status race fails closed.
This classifier is not a lock against future dispatches.

The resident response now has a bounded strict schema, duplicate/nonfinite JSON
rejection, UTC-aware observation window, typed service/container/inode fields
and internally consistent inode comparison. Unexpected fields and remote verdicts
are never copied to the final report. Valid observations are published only
after OCI/Actions/main postchecks pass. Failed postchecks cannot retain a
RECREATE_REQUIRED conclusion. Worker target and full process-idleness proof
remain UNKNOWN even with a valid mounted-credential response.

Timeout hierarchy: per-command default 35s, outer SSH process 105s, local SSH
wrapper 90s, remote Python timeout 75s with 5s kill grace. Remote Docker-exec
child cancellation and total-window enforcement still need independent review;
no cancellation behavior is claimed from mocks.

29 local unittest cases pass, including a fully mocked before/probe/after
roundtrip, self-run forgery, extra busy run, postcheck failure, stale response,
secret-canary rejection and mismatching inode. No live calls were used in tests.

Not ready for dispatch: workflow still disabled/unwired, entry point disabled;
runtime identity/configuration, comprehensive process inventory, approved
transport setup and independent exact-code review remain outstanding.
