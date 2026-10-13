# Disposable subset SQL crash/retry acceptance

This test candidate closes the PostgreSQL evidence gap left by scripted worker
transport tests. It changes no author worker source, production migration, role
grant, or runtime entrypoint. All source IDs, output receipts, accounts, and credentials
are synthetic. No production DSN or secret input is accepted.

The one-shot branch workflow uses the existing public CI service pattern:
Ubuntu 24.04, pinned PostgreSQL 18, localhost only, one bounded job. The schema
and pure fixture builders are reused from immutable public commit
`744d068a2cddfb8347411c8e4fb2bd69d19c09ab`. Only migrations 0056–0060 are installed
in the initially empty service. This is not a re-run of install/recovery suites.
The installer is the disposable service administrator; it does not qualify a
managed production installer. Runtime calls use distinct actual capability
logins with no superuser or direct table-write authority.

The exact author V2 patch is included as an immutable test input, SHA-256
`f2394f9c5dd9b6e829a9b85462496754f3b1e0594b9c93aa0f777752e5a5e96c`.
CI applies it to a separate checkout at `1489af80e9a10b8de8726f845aaf1ea07a700731`.
All authorization, finish and technical retry calls below run the real V2
`SubsetWorkerAdapter`; schema and helper functions remain pinned independently.

Six acceptance cases execute real SQL:

1. Concurrent identical V2 finish calls: hold the synthetic row briefly, observe
   both real lock waits, release it, require one commit and `SUBSET_LEASE_LOST`
   for the loser and subsequent replay.
2. Finish from an old lease after reclaim: require rejection without changes
   to jobs, batch, events, admission, reservation, approval, or observations;
   complete once under the new lease and fresh observations.
3. V2 client process exits after finish SQL but before commit: backend disconnect
   rolls back the complete snapshot, including post-work observation consumption;
   reclaim with a new lease and complete once.
4. V2 client process exits after commit without returning a receipt: terminal state
   survives, replay is rejected and cannot append another terminal event.
   This models client death, not packet-level loss of the COMMIT acknowledgement.
5. Three technical retries: distinct leases, two queued retry timestamps, then
   one FAILED event; no fourth claim.
6. Three expired crash leases: one FAILED event, no fourth claim and no mutation
   from repeated claim attempts after exhaustion.

Every fixture has a canary and a held sibling. No sibling may leave
`PENDING_CANARY` or have an attempt. Runtime use of legacy `finish_job` must be
denied by 0060 ACLs; valid terminal completion uses `finish_subset_job`, which
calls the unchanged legacy implementation within its transaction.

Lease expiry and retry readiness are accelerated by narrowly scoped synthetic
administrator timestamp updates. These cases do not test 900-second timing,
storage/power failure durability, media processing, Drive, or the full worker pipeline.
The child injects exit only at the real connection context boundary; database
execute, fetch, commit and rollback are not simulated.
Snapshots cover logical records and events, not nontransactional sequence gaps.

`python database/tests/subset_crash_retry/acceptance.py` is an offline description.
Execution requires POSIX, explicit `--execute`, the disposable marker, a verified
`_schema` checkout, and an empty fixed localhost database `subset_crash_retry_ci`.
No arbitrary database URL is accepted. CI logs preserve source SHAs, six case
results, snapshot digests, final connection/lock census and service destruction.
No artifact-storage upload, production activation, or promotion is included.

Current status: prepared acceptance candidate; a passing real CI run is required
before claiming SQL proof. Discard this branch to retire the test candidate;
the workflow destroys its own ephemeral service on both success and failure.
