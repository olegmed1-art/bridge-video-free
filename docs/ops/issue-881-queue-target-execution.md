# Issue 881 queue target execution

The host command authenticates with the existing worker principal. It must not
gain base-table or sequence privileges to inspect mutable event state.
The operator must perform this independent owner read on the explicit Neon
project `misty-poetry-18012774`, production branch `br-wispy-lab-b1rq54of`, database
`neondb`, immediately before issuing either initial check or apply:

```sql
SELECT current_setting('neon.project_id') AS project,
       clock_timestamp() AS observed_at,
       current_setting('neon.branch_id') AS branch,
       current_database() AS database,
       s.last_value, s.is_called,
       (SELECT max(event_id) FROM video_queue.job_event) AS max_event_id,
       (SELECT count(*) FROM video_queue.job_event) AS events,
       (SELECT count(*) FROM video_queue.batch) AS batches,
       (SELECT count(*) FROM video_queue.job) AS jobs
FROM video_queue.job_event_event_id_seq s;
```

This initial installation requires all three counts to be zero, `max_event_id`
NULL, `last_value=1`, and `is_called=false`. Otherwise stop and reconcile the
changed state; do not reset the sequence or widen runtime privileges. Confirm
the exact main SHA, no concurrent infrastructure operation, and clean review/CI.
Record the timestamped result and main SHA in issue #881 before the host command.
Use a comment beginning exactly `ISSUE881_QUEUE_OWNER_ATTESTATION_V1` followed
by one newline and a JSON object with the query's fields plus `main` (the exact
SHA). The workflow verifies the owner's immutable user ID and login, exact
identity/state, and both observation/comment timestamps. Evidence expires after
five minutes; the host rechecks expiry immediately before replacement. A stale
or missing report requires a new independent owner query and report.
The host independently rejects noncanonical sequence bounds, increment, cache,
cycling, type or sequence set, along with schema/ACL/role drift and non-idle work.

After a successful apply, repeat the owner read and independent host queue
diagnostic. The receipt must distinguish host file replacement from resident
container recreation. This operation does not authorize a new pre-canary.

Initial owner evidence at 2026-09-07 06:26 UTC: production events=0,
max_event_id=NULL, last_value=1, is_called=false; configuration independently
matches the rehearsal branch. This historical evidence is not the fresh
pre-command check required above.
