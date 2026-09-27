# Native stage startup and clock alignment — 2026-09-27

Status: PR #2040 merged; follow-up closes delayed-Popen admission gap.
No new production window or restore accepted.
Base: `7997b62d250bf48abda29553ec3b47a132d50758` (PR #2039).
ASSURED / I2 required for the exact reviewed tree before promotion.

## Evidence and purpose

Restore run `36341738178` refused at 18:46:45 UTC with `RPC_TIMEOUT` under
an 80-second runner exchange. Independent registry observation reported active
with updated_at 18:46:46 UTC. This is evidence of work continuing after the
runner's refusal, not proof of a hang, a complete restore, or its full duration.
The newer read-only rehearsal `36344248607` passed; it was not a full restore.

Equal 80-second durations were not equal deadlines: the runner started before
SSH, bootstrap, unit activation, source extraction and host authentication. A
constant increase alone cannot establish nesting across those start offsets.

## Fixed profile and admission

| Clock / admission | Seconds | Starts / meaning |
| --- | ---: | --- |
| Host run authorization | 120 | Host run-binding construction; never renewed |
| Stage PID1 runtime | 140 | Transient service activation |
| PID1 stop grace | 2 | Fixed control-group SIGKILL fallback |
| Stage wrapper | 148 | Before systemd-run; finally cleanup remains required |
| Cleanup allowance | 10 | Two systemctl calls, each capped at 5 |
| Transport admission margin | 12 | Capacity reserve; not a realtime guarantee |
| READY required remainder | 170 | `max(140+2,148)+10+12` on ORIGINAL runner RPC clock |
| Startup allowance | 40 | SSH/bootstrap/authentication plus runner admission check |
| Runner RPC | 210 | Before source/envelope transmission; not reset at READY |
| Completion reserve | 60 | Admission headroom for exit, readback and independent drain |
| Prelaunch required remainder | 270 | Before creating SSH process |
| Whole launcher | 310 | Before private request import: 40 preparation + 270 |
| Stage/rehearsal job cap | 480 | GitHub job, including checkout and dependency preparation |
| Job clock margin | 30 | Subtracted during one-time authenticated UTC projection |

The verified host sends a binding/request-specific READY only after its exact
140-second PID1 identity is checked. The runner authenticates its original run
again and requires at least 170 seconds still available before sending START
on BOTH the original RPC clock and the original job-clamped launcher clock
minus the 60-second completion reserve. This second comparison is essential:
Popen runs between prelaunch admission and channel construction and can consume
time before the RPC clock starts. Neither clock is reset or extended.
The host checks START, supervisor and its original authorization again before
entering the production runtime. The claim and production operations remain
inside that runtime, after this exchange. A late startup, wrong binding or
second admission poisons the channel. No retries or deadline renewal exist.

At READY, PID1 and wrapper clocks have already started. Their full original
caps therefore conservatively bound their possible remaining lifetime; no
cross-machine monotonic comparison or assumed SSH start offset is necessary.
The host's 120-second authorization is a ceiling, not a promise of 120 usable
seconds: earlier PID1/wrapper/Agreement deadlines always win.

The runner's authenticated job started_at is projected once onto local monotonic
time. Its deadline becomes the smaller of original 310 seconds and job expiry
minus 30 seconds. The timestamp must remain identical; malformed, future or
expired timestamps refuse, as do subsequent wall/monotonic deviations over
5 seconds. Reads cannot renew that deadline. Thus slow checkout/dependency
preparation reduces admission capacity instead of silently consuming the
outer job budget. The exact workflow hashes include both 8-minute job caps.
This projection assumes initial GitHub/runner UTC agreement within the margin;
it does not prove clock synchronization or prevent operator cancellation.

## Preserved boundaries and limitations

Legacy managed transports retain PID1 100 / wrapper 108 seconds, three-second
kill probes, generic RunBinding 60 and generic Channel default 60. Only stage
and rehearsal select the fixed managed_stage entrypoint. Lifetime remains a
standalone bootstrap module; source-bundled budgets may import it, not vice
versa. Previous supervisor drain uses recorded identity independently of the
new cap. Auxiliary fetch/candidate/inspect/drain wrappers retain their existing
115-second subprocess cap and legacy service profile.

Timeout reserves are admission margins, not evidence of cleanup. Scheduling,
external cancellation, network loss, a stuck kernel or a remote operation
already accepted can outlive local observation. Final PASS still requires
actual host exit, accepted head/unit readback, independent cgroup drain and a
fresh live run/source check. A 60-second final reserve does not guarantee all
115-second-cap auxiliary reads finish; the original launcher must remain live
for PASS. There is no retry, inferred success or automatic registry repair.

Agreement, single-use request, source/run/attempt/job, SQL, HOLD and workflow
state barriers remain. No SQL grants, pilot, service restart, activation or
live prepare/restore are part of this patch. The old failed restore requests
remain quarantined. The source-bound v5 package/preview prepared for 7997b62
must be regenerated after any new main; its old acceptance cannot be reused.

## Validation and rollback

Tests cover real-pipe READY/START admission at the latest allowed boundary,
slow startup/authentication, malformed binding, repeated admission, unchanged
absolute deadlines, expiry while waiting, and refusal before runtime entry.
Job-clock tests cover old/missing/future/changed timestamps, wall-clock jumps
and no renewal. Standalone bootstraps, mode routing, legacy profiles, exact
PID1 properties and source dependency closures are checked. A systemd-only
contract test actually starts the new stage profile and verifies its identity;
existing descendant/launcher-kill probes retain their three-second cap.

Local tests without systemd cannot prove host kernel behavior. Exact-tree CI
and I2, followed by a source-bound read-only rehearsal, are separate evidence.
A successful complete restore in a fresh, explicitly accepted owner window
is still required to establish operational recovery. Roll back by reverting
this patch and retaining journals/evidence; rollback does not authorize reuse
of an old request or window.


Follow-up review: a delayed Popen counterexample was found after PR #2040's
successful read-only rehearsal. Positive launcher lifetime alone was insufficient
for START admission. The follow-up checks both original deadlines together and
tests a fresh 210-second channel with an insufficient old launcher/job deadline,
including the real launcher assembly: no START is sent. The d09b011 v6 packet
is superseded upon the next merge and must not be used for a full-stage request.
