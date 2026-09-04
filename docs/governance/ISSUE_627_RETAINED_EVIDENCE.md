# Issue #627 retained evidence

Primary pull request: #1067
Duplicate implementation: #1068 (not to be merged separately)
Rebase checkpoint: `a28d01cb251c8e6153a3d2ecece84e59243d8e36`
Previous #1067 head retained at: `archive/issue-627-pr1067-b22c5b1` (`b22c5b11ae259dad9cabcd7cdd323874e15e42b0`)

## Consolidation decision

The current `main` implementation supersedes both old branches. The executable
collector/evaluator/consumer files from #1068 are intentionally not carried
forward: they form a competing contract and the #1068 observer detection had a
reviewed false-IDLE defect. Its useful tri-state documentation is retained here
only after reconciliation with the implementation now on `main`.

## Fail-closed contract

The classifier emits exactly one v2 proof with `BUSY`, `IDLE`, or `UNKNOWN`.
The authorizer accepts only a complete, canonical, fresh `IDLE` proof.
`BUSY`, `UNKNOWN`, malformed output, missing telemetry, stale telemetry,
future timestamps, probe errors, and contradictory evidence all deny STOP.

Authoritative workload evidence includes:

- Assistant Lab jobs, control commands, research jobs and research child work;
- Autopilot active tasks;
- Universal Video Neon queue plus inbox/running local spools;
- Observer pending/running spools and experiment processes;
- DDS3 pilot/main mass systemd units;
- Universal Video and DDS3 TLS scheduled maintenance services;
- host and database operator/maintenance leases;
- service/process/database/clock telemetry required to prove those sources.

An unavailable sibling source remains `UNKNOWN` even if another source is
positively `BUSY`; UNKNOWN precedence prevents partial telemetry from being
misrepresented as a complete result.

## Race boundary

Durable request workflows use per-request concurrency groups so GitHub cannot
replace an older pending request. Their bounded host mutation entrypoints acquire
`/run/lock/oracle-workload-mutation.lock`. Scheduled Universal Video
maintenance and DDS3 TLS renewal acquire that same host lock. Direct lifecycle
and non-durable workload mutations retain the shared non-cancelling GitHub
mutation fence. STOP performs a second fresh host proof while holding the host
fence at the final boundary. This work does not enable or schedule automatic
STOP.

## Required merge evidence

Close #627 only when the exact #1067 head has:

1. completed Oracle idle guard CI successfully;
2. fresh Codex review with no unresolved P0/P1/P2;
3. mergeability against current `main`;
4. retained exact-head SHA and run/review links in the PR conversation.

No Oracle lifecycle action, workload execution, training, or production
migration is part of this evidence procedure.
