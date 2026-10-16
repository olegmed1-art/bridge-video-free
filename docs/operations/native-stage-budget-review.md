# Fixed native stage budgets after the restore timeout

On 2026-09-27, source 9c021551 completed prepare run 36332757800 but
restore run 36333255394 reached the RPC deadline. Its runner recorded
75,218 ms total binding time, including 41,631 ms waiting for host messages.
Those overlapping measurements do not identify the exact host operation.
The restore request remains consumed and quarantined. Registry was restored
separately, with independently observed active state and unchanged DB BEFORE.

The former 60-second RPC/host and 100-second launcher limits were implementation
contracts, not a user or governance ceiling. This is an explicit reassessment
of those contracts, rather than removal of deadline checks. Further latency
optimizations alone cannot establish sufficient time for the longer restore.

| Clock | Fixed limit | Starts |
| --- | ---: | --- |
| Launcher run binding | 160 s | Before private request import |
| Runner RPC channel | 80 s | Immediately after SSH process launch |
| Host RPC channel | 80 s | After authenticated host bootstrap |
| Host stage run binding | 80 s | Inside the fixed host entrypoint |
| PID1 host supervisor | 100 s, unchanged | Host unit start |
| PID1 kill grace | 2 s, unchanged | Host unit stop |
| Host wrapper wait | 108 s, unchanged | systemd-run invocation |

Immediately before launching SSH, the authenticated runner must have at least
125 seconds remaining on its original launcher clock. This provides 80 seconds
of RPC work plus 45 seconds of headroom for completion and independent readback.
Preparation taking more than 35 seconds refuses before launching a host or
claiming the request. Both RPC endpoints opt in to the fixed stage budget;
other channel users retain the 60-second default. Both stage and read-only
rehearsal use the same fixed timing profile. Constants travel in the exact-source
bundle. No workflow input chooses a timeout.

These are separate absolute clocks, not renewable leases. The runner RPC clock
starts first and normally expires before the host's clocks. Admission headroom
does not prove that SSH startup, API service, remote cleanup or readback finished.
Only actual successful process exit, independent OCI readback, cgroup drain and
final fresh run/source observations permit PASS. Failure still requires separate
reconciliation; no retry or restoration is inferred. PID1's hard host bound is
unchanged, including when the runner or SSH disappears.

The original coordination Agreement remains an earlier authority cutoff whenever
it expires first. Every existing source/run, Agreement, HOLD, workflow pause/drain,
DB, checkpoint/CAS and single-use claim guard remains. SQL timeouts and statement
permissions are unchanged. No old request, head or failed scope becomes reusable.

Validation covers real pipe expiry and failure latching, legacy default limits,
nonrenewing run clocks, late/cancelled completion and slow prelaunch refusal
before process creation. Existing supervisor tests exercise crash, runtime expiry
and process cleanup. Exact-head CI, I2 review and a fresh read-only live rehearsal
are required before a new separately reviewed effectful request. A successful
rehearsal remains an estimate, not proof of full restore duration.

Rollback: revert this budget change to restore the former fixed stage clocks;
retain all historical failed requests and journals. A revert does not repair or
authorize any past operation.
