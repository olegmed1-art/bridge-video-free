# Light 0355 admission, operator procedure

This is a separately reviewed production action. The SQL is deliberately under
`ops/`, outside automatic migration discovery. It changes only the body of
`autopilot.claim_next_task(text,integer)`; it does not reset the canary, open a
route to Oracle PostgreSQL, or change any dispatch/receipt row.

## Entry gates

1. Confirm the target PR #1769 remains at
   `2586929313ab40326d64353b513ff86e5ae3350c` and no other operator is
   running a release, route or database cutover.
2. Dispatch `.github/workflows/oracle-light-held-runtime-preflight.yml` from
   the **exact current main**. Require the live `probe` job to PASS. A passing
   pull-request `contract` job is insufficient. Save the run URL, installed
   release `3244f4d4b17ce99e58c342f01e4715436a09622b`, service UID,
   invocation ID, queue inventory, fence digest, broker pins and manifest.
   If `NeedDaemonReload=yes`, an authorized host administrator runs
   `sudo systemctl daemon-reload` on `autopilot-lite-vnic`, without restarting
   the worker. Verify the loaded WorkingDirectory, HOLD environment, PID and
   invocation ID are unchanged, then repeat this live preflight and require
   `need_daemon_reload=no`. Do not infer effective config from files alone.
3. An authorized administrator stops only
   `school-autopilot-production-light.service`. Confirm `ActiveState=inactive`
   and `MainPID=0`; keep the HOLD drop-in and the known-good release untouched.
   Freeze concurrent schema deployments. Re-read route `neon/epoch0`, queue,
   leases, outbox and service identity immediately before the SQL transaction.

## Bounded change

From the reviewed commit, an authorized database administrator executes
`psql -X -v ON_ERROR_STOP=1 -f ops/autopilot_light_0355_retire.sql` against
the **existing production Neon database**, using the normal schema migration
identity. The SQL obtains the schema migration advisory lock and task/outbox
table locks, insists on the single exact READY canary and unchanged fenced
function, restores the saved pre-0355 body and verifies it before commit.
Capture command exit status and a fresh `pg_get_functiondef` digest; leave
the 0355 ledger and its backup table intact for recovery. A failed guard means
no change was committed. Never use the original 0355 rollback: it rejects the
READY queue and drops the saved baseline.

After successful commit, an authorized administrator switches only the HOLD
drop-in admission mode to ACTIVE under the reviewed deployment path, reloads
systemd, and starts the exact verified release. Attest PID, revision,
environment, worker ID, route, broker and stable startup. If these fail,
stop the unit; do not claim the canary by hand.

## Observation and recovery

Read the one existing work/task lineage and assert one fresh outbox, one
committed send intent, one authenticated owner command, ACK/SENT and a genuine
dispatch-bound terminal comment with CALLBACK_ACCEPTED. Check for zero repair
follow-ups and duplicate commands. A BLOCKED result proves transport only.
Do not replay or resend an ambiguous/expired dispatch.

Before any publication, recovery is: stop the unit, restore HOLD, and execute
`psql -X -v ON_ERROR_STOP=1 -f ops/autopilot_light_0355_refence.sql` using the
same authorized database identity. This script requires the one untouched
READY canary and no outbox for it, then reinstalls exactly the original 0355
patch from its saved backup. Preserve the ledger and backup. If publication has
happened, do not run this pre-publication script: stop admissions and keep
all outbox, intent and receipt evidence; diagnose without resetting them.
The old release can be restored under HOLD only after verifying its mailbox
incompatibility remains fenced. Any drift or unavailable administrator blocks
admission and requires a new review.
