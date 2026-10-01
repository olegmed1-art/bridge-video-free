# IBM trial executor: review branch, dispatch awaits coordinated window

Scope: instance `02c7_4463831b-c1a7-45a7-84f4-c0388c8e03b2`, eu-de;
API 2026-09-22. No new resources, keys, IAM roles, guest access changes or deployment.
Owner accepted old-workload risk and a $10 limit. Parent accepted the base price
and explicitly authorized removal of the two temporary preparation locks only.
`LIVE_START_ENABLED=True`; workflow remains manual, mode- and branch-scoped.
Live dispatch is NOT authorized in this preparation turn. Parent coordinates it.
No operational invocation is authorized as part of this code preparation.

## Explicit second window: manual console trial (owner approval 09:07:14 UTC)

Owner separately approved a second single window for personal serial-console
diagnosis, at most 10 minutes with the same $10 budget and accepted old-work risk.
This does not extend the earlier short RDC trial. That first trial ended with
three STOPPED observations by 08:51:46 UTC in run 36838730063; RDC never connected.

New exact dispatch inputs on this review branch:
`mode=manual_console_trial`, `test_oracle=false`, workflow
`ibm-vpc-power-probe.yml`. CLI mode has a distinct acknowledgement
`OWNER_APPROVED_MANUAL_CONSOLE_10MIN_10USD`. No dispatch is performed in this turn.

This mode uses the SAME target, backup, stopped/startable, IAM and >=900-second TTL
guards and one Start/ordinary Stop semantics. It omits ONLY the 120-second RDC
cutoff. The absolute clock still starts before Start POST; boot time consumes the
600-second budget. Polling crosses the 565-second containment threshold with the
same 35-second I/O reserve. The ordinary Stop request is bounded by 600 seconds
under the existing process/network assumptions; actual shutdown can take longer.
No time or budget reset at RUNNING, console connection or RDC arrival.

The owner opens the existing serial console personally. Unknown password, denied
rights, work activity or loss of observation => parent immediately dispatches
`mode=trial_stop`, `test_oracle=false` on the same ref. No password/role creation,
key/IAM changes, guest service changes, forced console takeover or force Stop.
The executor does not infer console success or RDC success. RDC may be passively
observed; its absence alone does not terminate a usable owner-console session.
The old six-service-stop instructions below apply ONLY to the earlier RDC trial,
not to this read-only manual-console diagnosis window.

Independent Stop remains a separate dispatch with its own concurrency and contract
job. Parent must keep its proven browser dispatch route available throughout. For
this separately approved manual mode, the earlier t=300 supervision instruction
does not apply: parent dispatches on completion/refusal immediately and at t=540
at the latest if still active, while the main timer independently contains before
t=600. Neither GitHub scheduling nor a hard billing cap is guaranteed; queued Stop
requires prompt ordinary Stop in the IBM Cloud instance management UI (not a guest
shell command requiring the unavailable login), not waiting past the deadline.
No new infrastructure or automation is provisioned.

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

- `mode=trial_start`, `test_oracle=false`: fresh exact stopped/startable and exact AVAILABLE backup
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
STARTING, unknown POST, absent queue readback, retained lock mechanism, timers, 403 and
no-secret output. A real POSIX wall-timer smoke test is skipped on local Windows
and runs in the Ubuntu contract job before any future secret-bearing control job.
Independent I2 review is required before publishing.
Parent authorized the narrow Start-unlock code change; publication does not
authorize live dispatch during preparation. No operator provisioning is needed.

## Coordinated window and division of responsibility

Both dispatches use workflow `ibm-vpc-power-probe.yml`, ref
`review/ibm-trial-control-20261001`, `test_oracle=false`. Start uses
`mode=trial_start`; independent ordinary Stop uses `mode=trial_stop`.
Parent verifies this reviewed branch SHA immediately before dispatch; do not
change the branch while the window is active. Neither dispatch is run here.

The GitHub Python trial process polls every 5 s. Its monotonic clock starts before
the Start POST, and first observed RUNNING sets a second clock. The process enters
containment using thresholds of 565 s from Start or 85 s from first RUNNING (35 s
I/O reserve), whichever comes first; a poll/in-flight read can delay threshold
detection. It requests ordinary Stop within 600/120 s under the bounded
I/O assumptions. It does not wait for guest success and does not call RDC from CI.
Stop observation is up to 180 s, with a possible final in-flight request/poll overrun;
ordinary shutdown completion, GitHub scheduling and total bill are not hard-capped.
Each HTTP operation has a 10 s wall deadline. Start job timeout is 16 min; Stop job
5 min; their own preceding contract jobs each have a 5 min timeout.

The parent/current interactive executor owns the independent Stop dispatch and
the guest RDC calls. Before the live window it must have both dispatch and run/log
observation tools callable, and arrange an active supervisor for the entire window.
It dispatches Stop immediately on unsafe guest/activity/observer outcomes, and
at t=300 s after START_SUBMITTING at the latest if terminal STOPPED is not confirmed.
If that log timestamp cannot be obtained, use the earlier Start dispatch timestamp.
This is an explicit supervised deadline, NOT an automatic independent watchdog.
Check that the independent run actually reaches its Stop job; queued dispatch is
not proof. GitHub scheduling has no 300 s bound. If queued/unavailable, use the
owner's existing IBM console ordinary Stop promptly; do not wait until t=600.
Do not cancel the primary in place of Stop or retry an uncertain mutation blindly.

Tool inventory in this delegated environment exposes GitHub run/job/log reads but
NO workflow-dispatch action. Therefore this executor can own guest RDC observation,
but cannot promise to dispatch independent Stop here. The parent must own both
dispatches through its previously proven route and remain active for the window;
if that route is unavailable, do not start the coordinated trial. No new credential
or hidden API route is introduced to compensate for this tool limitation.

RDC inventory read on this preparation turn: `list_devices` succeeded; exact IBM
device `dc66e102-a6cc-43a2-b597-1e8c2a0fe66d` Offline, Light Online.
`start_process` and process-output tools are available, but guest reachability,
UID/root privileges and actual service names remain unverified until IBM reconnects.
No IBM guest command was attempted here. On reconnect the interactive executor
uses that exact deviceId, `id -u`, and only if needed one `sudo -n id -u`.
Denial/NO_NEW_PRIVILEGES/no reconnect within the bounded window => independent Stop.

If existing root is confirmed, the interactive executor (not GitHub CI) submits
one non-blocking temporary systemd stop of these exact six units, using `sudo -n`
only if the existing privilege check succeeded:

```
systemctl stop --no-block assistant-lab.service assistant-lab-observer.service assistant-lab-control.service assistant-lab-control-bridge.service universal-video-container.service bridge-ben.service
```

Read only LoadState/ActiveState/SubState/MainPID/ControlGroup/Result for those units
with `systemctl show`, then numeric PIDs from their validated existing cgroup paths.
Do not output environment, full process arguments or journal/credential contents.
Any missing/failed stop, residual worker, work activity or lost observation triggers
independent Stop immediately; no disable/mask, worker, install or repair script.
Parent checks Neon/queues independently through its existing connector. The timer
continues while guest calls happen and stops even when guest checks succeed.

## Token-lifetime hardening and price composition

Immediately before the Start POST, after preflight, the same verified IAM token
must have at least 900 s remaining: 600 s trial + 180 s Stop observation + 120 s
margin. A short token fails closed before Start; no refresh/retry added. Independent
Stop retains the existing >60 s IAM check and is not blocked by the 900 s Start gate.

The catalog charges `part-is.instance-hours-bx3dc-8x40` per whole Instance-Hour,
not per vCPU or GiB. IBM defines this profile as 8 vCPU, 40 GiB RAM and 1x260 GB
instance storage. Gen 3 documentation says local instance storage is included in
all profiles. Together these support treating $0.52605/hour as the base profile
bundle, not multiplying by 40 or adding 260 storage units. Persistent boot/block
volumes, image storage, licenses/confidential modes/network/tax are not included
in this base-cost claim. Profile sources:
https://cloud.ibm.com/docs/vpc?topic=vpc-confidential-computing-vsi-profiles-gen3-x86
https://cloud.ibm.com/docs/vpc?topic=vpc-profiles

Fresh public re-read again returned USD 0.52605/hour and no separate RAM/instance
storage metric within this product, but returned effective dates 2026-09-01 through
2026-09-30, unlike the earlier saved October receipt above. USD amount agrees;
current-period applicability is not independently settled by that stale response.
No account credentials used and no guaranteed $10 billing cap claimed.
