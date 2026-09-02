# Fresh read-only audit for issue #627

Base audited: `main` at `1f866040f407f4a3c2571fa6c44554dfc9b95f79`.

Observed before this branch:

- `assistant_lab/oracle_idle_schema.sql` counted `assistant_lab.job`, `assistant_lab.research_job`, and `assistant_lab.control_command`, but treated only research stages `QUEUED`/`RUNNING` as active.
- `ops/oracle_idle_state.sh` treated Universal Video Neon telemetry as optional when the queue DSN was absent, so missing video telemetry could be interpreted as zero jobs.
- The existing classifier did not require fresh per-source timestamps and had no general conflict detector.
- Local Universal Video `inbox`/`running` spool state and resident `universal-video-resident-status-v2` were not part of one complete proof.
- BEN/bulk/other workload-family evidence was not exposed independently, although `assistant_lab.job` was an umbrella queue.
- Operator/maintenance lease freshness was not part of `main`.
- The repository contained no production-wired automatic STOP in the audited guard path; the new consumer remains an unconnected decision contract and contains no OCI command.

Related historical PRs #1020/#1021 were reviewed as context, not authority. Their useful fail-closed changes were re-evaluated against current schemas rather than copied wholesale.

The new contract requires complete, fresh, consistent telemetry for every registered family before `IDLE` can exist. Any source gap is `UNKNOWN`.
