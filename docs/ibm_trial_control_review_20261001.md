# IBM trial executor: review branch, LIVE START LOCKED

Scope: instance `02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2`, eu-de;
API 2026-09-22. No new resources, keys, IAM roles, guest access changes or deployment.
Owner accepted old-workload risk and a $10 limit. Parent must finish cost review
and explicitly release Start. Code currently enforces two independent Start locks:
`LIVE_START_ENABLED=False` and workflow trial-start `if: false && ...`.
Start refuses BEFORE reading credentials/authentication; stop-only remains usable.
No operational invocation or real IBM API call was performed during preparation.

## Implemented existing control channels

`ops/ibm_trial_executor.py` uses existing GitHub `IBM_CLOUD_API_KEY`, verifies the
existing IAM identity/account/expiry via the proven oracle-probe verifier, and
uses the already proven direct GitHub -> IBM regional API route. There is no
Light/SSH dependency for power control. Token remains in process memory.
No-redirect HTTPS, fixed target paths and bounded secret-safe response handling.
On the pinned Ubuntu runner a POSIX wall timer bounds each entire HTTP open/read,
not only socket inactivity. Platforms without that timer refuse operational calls.

The existing registered `.github/workflows/ibm-vpc-power-probe.yml` gains two modes
on `review/ibm-trial-control-20261001` only; original status/test_oracle remain default:

- `mode=trial_start`, `test_oracle=false`: locked pending parent release. Once
  separately enabled, fresh exact stopped/startable and exact AVAILABLE backup
  checks precede one `POST .../actions` with `{"type":"start","force":false}`.
- `mode=trial_stop`, `test_oracle=false`: separate manual workflow dispatch and
  independent `ibm-vpc-independent-stop` concurrency lane. Fresh authentication,
  exact GET, at most one `{"type":"stop","force":false}` POST from running,
  starting or restarting, then bounded observation. It never invokes Start.
  This run does NOT need primary completion, primary artifacts, Light, guest SSH,
  or new rights. It only depends on its own offline contract job and GitHub/IBM.

Existing workflow registration on default main permits dispatch using a branch
ref; no new default-branch workflow installation is requested. Do not open a PR
or dispatch as part of code publication. Publish [skip ci] and inspect run count.
To stop during an approved future trial, dispatch the same workflow on this branch
with mode=trial_stop, test_oracle=false. Do not merely cancel the primary run.
A GitHub-wide outage or an IBM API outage can affect both channels; then the
already authenticated owner IBM console's ordinary Stop remains the manual path.
No force stop and no guaranteed hard shutdown time are claimed.

## Bounded trial behavior and guest observation

The primary trial reserves HTTP/poll time and requests ordinary Stop before 600 s
from Start submission. After first observed RUNNING it stops within 120 s regardless
of guest success, so no new cross-channel extension/attestation mechanism is needed.
The reduced window is within the authorized maximum ten minutes. It then polls for
up to 180 s; delayed shutdown is a reported unresolved outcome, not a forced stop.
A failed/malformed/uncertain Start response is never retried; containment reads run
in finally. Unknown Stop POST is never blindly repeated within an invocation.
Primary failure/cancellation is why the separate stop dispatch exists.

Parent supervises guest containment through existing authorized IBM RDC device
`dc66e102-a6cc-43a2-b597-1e8c2a0fe66d` (currently Offline; reconnect unproven).
After Running, wait only inside the bounded window. Once Online, read actual UID;
if necessary make one noninteractive `sudo -n id -u` check of existing rights.
No passwords/new SSH users/keys/NO_NEW_PRIVILEGES workarounds. Failure to obtain
admin access, service-stop failure, work activity or lost observation requires the
parent to dispatch trial_stop immediately, rather than waiting for the timer.
The primary timer still stops automatically if no feedback arrives.

With existing admin rights, temporarily stop exactly: assistant-lab,
assistant-lab-observer, assistant-lab-control, assistant-lab-control-bridge,
universal-video-container, bridge-ben. No disable/mask/restart. Confirm inactive
states, MainPID and residual cgroup tasks. Parent independently reads Neon/queues
through its existing connection; do not extract DB credentials or execute workers.
Read-only diagnostics only after all six stop. Image backup does not roll back
external Neon writes or side effects from old work.

## Pending-action limitation (explicit, not fabricated)

Current official IBM SDK exposes create_instance_action but no
list_instance_actions/get_instance_action. Instance has status/startable; action
id/status/href are deprecated. Therefore GET /actions or GET /actions/{id} is NOT
invented or called. Stop uses ordinary queued semantics, including STARTING.
Stopped with startable=false is treated as possible outstanding work and observed.
Three stopped/startable observations produce STOPPED_OBSERVED, never a claim that
an action queue was read/empty. After ANY Start (even an accepted POST), stopped
without ever observing an active transition remains
STOPPED_BUT_START_OUTCOME_UNRESOLVED; the full containment window continues to
watch for a delayed transition before exit 4.
No-response/403/404 is never hidden as success. Definitive Stop 4xx remains exit 4
when later observation is stopped; all failures use fixed codes without raw bodies.
Queue readback remains an IBM API limitation, not a request for new permissions.
Do not re-run a trial or unknown mutation; reconcile observed state with the parent.

## Cost evidence (public, no account credentials)

Official catalog GET succeeded on 2026-10-01, effective 2026-10-01T00:00:00Z:
https://globalcatalog.cloud.ibm.com/api/v1/b9511f31-9b16-458f-9acf-c493819d3548:eu-deb1b71690/pricing

Metric part-is.instance-hours-bx3dc-8x40; Instance-Hour, quantity 1:
USD 0.52605/hour; EUR 0.45905143032/hour. Ten minutes base compute is
USD 0.087675 (EUR 0.07650857172). This is the ordinary profile metric, not a claim
that the account has no license/SGX/TDX/network/tax extras. Catalog also lists those
separate metrics. Credit applicability is unverified and is not used in this math.
IBM bills compute per second without minimum; persistent storage/network continue:
https://cloud.ibm.com/docs/vpc?topic=vpc-suspend-billing

These figures give ample base-compute margin within $10; parent separately checks
applicable extras. No claim of guaranteed total ceiling or zero workload risk.

## Validation and release

Offline tests mock network, clocks and provider responses, covering separate Stop,
STARTING, unknown POST, absent queue readback, hard Start locks, timers, 403 and
no-secret output. A real POSIX wall-timer smoke test is skipped on local Windows
and runs in the Ubuntu contract job before any future secret-bearing control job.
Independent I2 review is required before publishing.
Release requires explicit parent message and review of the narrow Start-unlock
change; publication does not release Start. No operator provisioning is needed:
the concrete independent channel is the existing GitHub dispatch described above.
