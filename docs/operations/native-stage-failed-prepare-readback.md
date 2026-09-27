# Failed native prepare: observational readback

On 2026-09-27 prepare run 36310581069, attempt 1, job 108595637618
failed with RPC_TIMEOUT on source 4bb6d2e082773d4119a1003b71e31e60d1277613.
No execute stage or Cloud pilot was dispatched. A timeout does not establish
whether the private prepare claim, unit receipt, or checkpoint was retained.
The consumed request must not be retried and no checkpoint may be inferred.

The rehearsal workflow's explicit `inspect` action accepts a canonical,
independently hashed diagnostic request binding current reviewed main, the old
source, old accepted request digest, and failed run/attempt/job. It verifies the
ended run and successful contract job, then reads the existing root-owned
prepare scope and private OCI checkpoints twice. Inspection requires the Light
service/config fingerprint to remain unchanged, but opens no database connection
and does not claim database maintenance admission.

The diagnostic input itself may be imported create-only into the private request
namespace and short-lived supervised diagnostic processes are started. Existing
claims, stage-unit receipts, journals, locks and OCI objects are never created,
repaired, deleted or overwritten. Missing locks or malformed private paths fail
closed. Inspection does not pause/enable workflows, grant permissions, or resume
any stage. Journal event labels describe historical records, not current workflow
state. Public output contains bounded metadata and digests, never private bytes.

Relations `no_head`, `exact_pair`, `local_uncheckpointed_suffix` and
`mismatch_or_unknown` are observations only; `resume_authorized` is always false.
Any recovery action needs separate review of the actual evidence and current
source. An expired coordination window is never renewed by inspection. A new
source invalidates the old execution handoff even if the observed pair is exact.

Validation: focused unit tests, independent I2 review and required CI before
merging. Rollback is reverting this diagnostic-only change; it cannot reverse
or reset an old stage and must not be used for that purpose.

## Observed result and bounded latency correction

Readback run 36311957929 / job 108599569650 succeeded on main
20c7284160fa0a80b9c473947eed6d50b3ff3c0a at 2026-09-27T10:17:29Z
in 54,986 ms. It observed the consumed claim and identical local/remote unit
64cb37c7ac06ef34285310d146060aadb1f209d25dc6e45d78124e3533392d13.
The operation journal contains BOUND; the pause journal contains PLAN and
DISABLE_INTENT. Head f4d5bbd66b4f10a24ad8ff6f6740281834a0c7f7f9b592a811db4d2261bc64ec
retains only the pristine prefix. Relation: local_uncheckpointed_suffix.
The registry workflow was independently observed active after readback.

Under the reviewed single-writer/CAS dispatch protocol, the disable PUT requires
a successful intent checkpoint first. That checkpoint was not published, so the
old prepare did not reach the workflow disable PUT. No execute or pilot was
launched. Keep this old scope and any orphaned archive untouched as quarantined
evidence; never adopt its observed head, truncate its local suffix, or retry the
consumed request. This record supplies no new execution authority.

Each full run observation previously performed five serial GitHub GETs. The
latency correction preserves the initial and final run GET, all identity/source/
job predicates and the absolute deadline, while joining the independent middle
workflow-blob/main/jobs GETs concurrently. Each GET has its own no-proxy,
no-redirect opener. All started workers are joined even on failure; there is no
retry, cached authority, or late job-ID promotion.

The dispatch path also removes one redundant full observation immediately before
its checkpoint, while retaining exact plan/phase/workflow checks and the
checkpoint's fresh pre/post observations. Both workflow dispatch checks and the
source check before PUT remain. Host/pipe limits, the outer launch budget, SQL
bounds, and coordination-window expiry are unchanged. Fault tests and independent
I2 review are required; a live read-only timing rehearsal still estimates rather
than proves production prepare duration.

## Later failure and diagnostic scope

Prepare run 36324504208 / job 108634592818 on source
6bc70f5cd82841f22381e7cb5cc40fb16eaa5e94 failed at 14:04:59 UTC with
RPC_UNAVAILABLE after 76,717 ms of the outer binding. Readback run
36324735524 / job 108635245946 found a local PREPARED record, but the
remote checkpoint did not include the whole local suffix. This does not
authorize resume. No execute/grants or pilot followed. The registry workflow
was restored separately after independent review and no-writer checks; all
attempt records remain quarantined. See issue1946 comment5856597337.

The RPC channel has its own absolute 60-second lifetime, distinct from the
100-second outer binding. The new `timing_profile` is diagnostic only. It
records bounded, fixed-label runner call counts, total milliseconds and maximum
milliseconds for GitHub GETs, OCI reads/writes, source checks, and RPC waiting,
store service and unit retention. It never records arguments, URLs, responses,
credentials or exception text. Failed calls contribute their elapsed time;
measurement failures mark the profile incomplete without replacing the original
operation or its exception. Existing calls, checks, order, retry policy and
deadlines are unchanged.

`operations` covers the whole runner; `host_exchange_operations` separately
counts calls that start inside the RPC phase, excluding prelaunch asset work
and final independent readback. Durations overlap: GitHub and OCI calls can be
inside RPC servicing, and independent GitHub reads run concurrently. Do not
add these totals as wall time. RPC waiting includes host work and communication,
not a measured host-only duration. The profile cannot establish successful
preparation or grant authority. First use a read-only rehearsal to identify
measured runner costs; that rehearsal still does not prove full prepare timing.

The workflow API inventory can also be dispatched manually with a required
`expected_main_sha`. The observation job admits only the exact repository/main
and owner/triggering-owner, with the input equal to the dispatched SHA. It
retains read-only Actions/Contents permissions and the existing bounded inventory
entrypoint. Obtain the restored registry's actual `updated_at` from this report;
do not guess it or treat the inventory as writer exclusion.


## Measured read service correction

Read-only rehearsal 36326264660 / job 108639559199 on d38d55e2 passed:
66,001 ms outer binding, 53,665 ms host elapsed against a 60-second RPC
lifetime. Within host exchange, runner counters recorded 160 OCI read calls
(18,538 ms), five writes (678 ms), and 55 GitHub GETs (12,616 ms). Durations
overlap, OCI calls exclude streamed object-body consumption, and the rehearsal
does not exercise production unit retention. This is not full prepare margin.

The next correction executes the four independent privacy observations in each
existing `assert_private` boundary concurrently. Launcher opt-in constructs five
SDK clients: one general and one for each fixed read, with distinct base clients,
HTTP sessions and signers. SDK 2.186.0 wheel source confirms fresh Signer and
Session construction; the facade also checks distinct identities. Per-read locks
and a group lock prevent overlapping reuse. Dedicated dispatch is restricted to
the privacy group; budget reads and other calls retain the general client.
Concurrent read errors can change refusal precedence, but every invalid result
still prevents effects. Other consumers retain the original
sequential path. All started readers are joined before returning or refusing,
including submission failure. No response caching, retries, writes in workers,
new deadlines, budget shortcuts or skipped privacy checks are introduced.
Lifecycle 404 alone means absent policy; malformed data still refuses. Original
pre/post-write authority checks and conditional-write/readback order remain.

`oci_privacy` reports wall duration of each complete privacy group. Its time
still overlaps other counters. A fresh read-only rehearsal after exact-head CI
and independent I2 review must measure benefit before preparing another
coordination package. Rollback is a revert to the prior sequential launcher.


## Preserve authority order while reusing transport

PR2032 merged as 47a8bd8. Rehearsal36328269201 / job108645195092 passed:
67,557 ms binding and 51,962 ms host. RPC store service improved from 26,408 to
21,666 ms; 28 privacy groups took 4,125 ms. GitHub GET summed durations rose
from 12,616 to 17,657 ms, so host improvement was only 1,703 ms. These separate
runs and overlapping metrics do not establish reliable full-prepare margin.

Independent I2 rejected batching the initial run observation with the middle
reads: the initial temporal bracket must remain. The next correction retains
all observations and their ordering and changes transport only. Stage launcher
and stage/rehearsal host opt into four fixed HTTPS lanes with verified TLS to
api.github.com:443, fresh GETs/headers and no proxy, redirect, cache or retry.
Each lane owns a connection under a lock; stable path classes retain reuse
across successive short worker pools. Drain/diagnostic reads use the default
lane. The existing sequential API remains available to other consumers.

A stale socket, timeout, non-200, pagination, malformed/truncated/oversized JSON
or duplicate key refuses and poisons the entire API. A failed GET is never
resent. Only a later distinct GET may reconnect after a clean server close.
All started observation workers still join; cleanup closes connections on host
or launcher completion. The four-second socket bounds and existing run/RPC
lifetimes remain unchanged. Tests exercise the actual stdlib HTTP parser over
controlled sockets. Another exact-main read-only measurement is required;
connection reuse does not itself prove prepare capacity or grant authority.
