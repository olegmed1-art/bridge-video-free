# Oracle Light first-lane owner cycle

Status: source integration under review; no new Oracle intake or RUN performed.
Base main: 9fad88758b1fc0f35c24ff05cf6c8bdb41fa62b4.

The existing publisher and finite supervisor needed one authenticated owner path
through prepare, publish, permit, execute, independent terminal verification and
DB-control restoration. The new workflow binds each accepted phase to current
main, its running owner job, the retained f82 runtime, exact plan and prior phase
records. Controller helpers have a separate package; the historical runtime
package remains a5e2c6557576280caa9fc8106779c1465f3f9cfed88442848fd7fa35ee0ebdd4.

Execution is fixed at 420 seconds; feed publication requires at least 600 seconds
remaining in the accepted window. Late launch refuses before staging the feed. Authenticated workflow/main checks run at a
15-second cadence during RUN; local process/admission/deadline checks remain at
two seconds. The startup HOLD wait does not poll remote APIs every half-second. PID1 cleanup denies admission and drains the
transient process independently of SSH. Original native HOLD restart follows
fresh terminal and DB restoration evidence. Driver flock ownership ends before
launching the supervisor. Neither credentials nor tokens are retained on disk.

Recovery covers a lost DB-restore acknowledgment and a lost HOLD-start response.
It classifies exact current rows before issuing SQL and verifies an already
restarted credential-free HOLD process. A new current-main owner run can finish
cleanup while preserving the original accepted runtime, plan and helper records.

Intake now saves a prospective receipt before COMMIT and uses a plan advisory
transaction lock. Publication must prove the exact committed DB graph; a durable
receipt alone never authorizes a broker call. Pre-execution containment takes the
same lock, disables native admission in DB, preserves can_repair=false and leaves
all task/outbox/work evidence intact. It reports CONTAINED_UNRESOLVED and forbids
later phase advancement. It is not cancellation or production readiness. A staged
feed or any native receipt requires separate incident reconciliation.

Verification includes isolated stdlib cleanup import, accepted-byte and root-file
checks, live workflow drift, phase ordering, driver-lock release, fresh-main
cleanup, prospective-receipt rollback refusal, DB lost-ACK reconciliation and
HOLD-start recovery. The disposable PostgreSQL18 fixture additionally exercises
actual intake RPCs, committed-graph publication admission, terminal restoration
readback and containment without closing the pending queue graph.

Oracle readback before changes: legacy PID410394/invocation
b9e107c5bae84f4f9982a0dffacf7a4b and native PID415582/invocation
e1dd0cc5eb804f68ad3e35731a910112, both active with NRestarts=0.
No claim of completed live work is made by this source change.

A substantive audit may complete with AUDIT_FINDINGS_REPORTED while its work
item remains BLOCKED under deployed migration0368/0372. Terminal verification
accepts that exact outcome without treating findings as a clean audit or
creating a successor. Before intake the controller verifies the deployed
terminal trigger against the retained0372 definition. PG18 rehearsal runs
both AUDIT_PASSED and AUDIT_FINDINGS_REPORTED through control restoration.
