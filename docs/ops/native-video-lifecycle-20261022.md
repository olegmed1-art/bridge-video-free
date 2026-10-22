# Native VIDEO lifecycle candidate — 2026-10-22

Status: source candidate; release HOLD. Nothing in this change activates a
production worker, installs SQL, creates a role/credential, grants access, or
invokes a database/container/recognizer. Base main: a993b69d931f88f84fac0c2dd3d02592f3d2e5ee.

## Changes and provenance

The prepared VIDEO candidate was absent from main. This branch imports only its
26 necessary Python prerequisites and SQL0063/0064 source proposals, then fixes
the lifecycle. Existing Books, teacher-package, recognizer, route and other
unrelated snapshot changes are excluded. The eight existing-file prerequisite
deltas preserve the private terminal path; new code has explicit opt-in ports.
No workflow, Vercel configuration or automatic migration runner is changed.

1. Acknowledged task heartbeats can repeat. A lost reply/COMMIT still poisons
   the original session and cannot trigger a reconnect or automatic replay.
2. Native processing requires an exact committed/enrolled claim and an explicit
   `NativeExecutionBudget`. The original provider builds it from a fresh SQL
   snapshot. Required work duration is supplied by the bounded processor contract,
   never guessed from a video name/frame count. The budget takes the minimum
   existing expiry, reserves cleanup, and expires by wall and monotonic clocks.
   The original five-minute cap and no-renewal-after-BIND SQL rules remain.
   Work that does not fit is refused before processing. The timeout is cooperative
   main-thread SIGALRM, not a hard container/RSS limit or proof of runtime speed.
3. `0065_native_video_terminal_v1.sql` proposes
   `autopilot.complete_native_video_task_v1(uuid,bigint,text,text)`. The original
   owner session verifies the immutable committed0063 receipt and exact task,
   epoch, source, former lease, Assignment, registry and approval. Task DONE,
   terminal fields and event commit together. An exact DONE duplicate returns
   the existing receipt without a new event/finish. Revoked/stale/conflicting
   inputs fail closed. There is no new GRANT in this proposal.
4. The preclaim completion port is created by the original owner session for its
   already-created task; it is disabled by default and does not need an Assignment
   before one exists. Native queue construction requires the explicit enabled
   port. After BIND/enrollment the original staged reader supplies verified receipt
   bytes: READ_VERIFIED -> processing DONE COMMIT ACK -> ACK_ATTEMPTED -> wire ACK.
   UNKNOWN completion stops before creating an ACK id or sending an ACK.
5. Owner acceptance and delivery are distinct. The existing SQLite owner ledger
   still records acceptance before the one-shot wire effect. Recovery returns
   `OWNER_ACCEPTED_DELIVERY_UNKNOWN` for an accepted ACK without a final head,
   `DELIVERY_CONFIRMED_READBACK` only for the durable final ACKED head, and
   `UNKNOWN_ACK_NO_RESEND` if acceptance is not found. Each includes separate
   `owner_accepted` and `delivery_confirmed` booleans. Persisted/wire receipt
   schemas and old receipts are unchanged. Consumers of the old recovery status
   `ACKED_READBACK` must update with this code version.

## Integration and authority boundaries

`process_one_private_neon` still contains a legacy claim path. For the exact
native worker it now refuses before legacy claim or I/O, with
`NATIVE_ORIGINAL_CLAIM_BINDING_REQUIRED`. This is an explicit integration HOLD:
the real native claim/BIND launcher must use the concrete `NativeVideoWorkerClaim`
and original journal, obtain genuine owner enrollment, build the budget, and pass
the original prepared handoff to `run_private_one_job`. The implemented direct
worker path carries that budget through to `process_claim`; no second claim,
synthetic token or default runtime binding is substituted.

An installed UID1001 owner / UID996 worker / IBM transport binding, real measured
or otherwise enforced work-duration bound, SQL target/LOGIN/ACL and exact release
bundle remain unqualified. These are not proved by the local protocol doubles.
In particular this branch does not install the recognizer or provide READ_RESULT.

## Verification and remaining execution

Regressions were written before the fixes. The second acknowledged heartbeat and
ACK classification defects were reproduced. Focused independent review used a
separate model and independently passed 47 cases: lease22, terminal15, physical
SQLite ACK4, terminal join6. It found a completion/enrollment bootstrap cycle;
the final preclaim owner-session factory fixes that cycle. No unresolved P1/P2
remains in that focused review. This is source/client assurance, not PostgreSQL
or native runtime qualification. The full regression passed116 tests in57.114 seconds (controller59.953s). Its
receipt is retained with the task evidence and includes the imported
journal/enrollment/store tests.

The historical real0063 run06 P01–P16 PASS is preserved. Native0064 run04 reached
N01 PASS and N02 ACL refusal; N03–N20 were NOT_RUN. The concrete ambient ACL object
is still unknown. Those facts do not qualify the new0065 transition.

The prepared diagnostic run05 remains useful only for the N02 catalog witness:
its unchanged ACL blocker is independent of these lifecycle fixes. It is not run
by this change and cannot qualify0065. Before any future Oracle run: obtain the
specific test security approval and a fresh occupancy/cleanup receipt. Then use
one consolidated engine plan including real concurrent claims, both heartbeat
outcomes, terminal DONE atomicity/duplicates, stale/revoked inputs and lost-COMMIT
no-replay. Any temporary roles/membership/GRANT/BYPASSRLS need the exact separately
approved test scope. No automatic repeat or UNKNOWN replay is permitted.

Future installation additionally requires approval of the exact target and
existing identities, dependency DDL, the reviewed0064 runtime ACL, and the new
owner-only EXECUTE on the exact0065 signature. Credentials, membership, ACLs,
services/socket binding and activation are separate actions. Reapplying the old
0064 privilege preflight after the new RPC grant needs a reviewed versioned
allow-list; do not silently relax the existing ambient-privilege guard.

## Publication and rollback

Publish only the isolated feature branch for coordinator integration review.
The local branch/path trigger screen found no matching push workflow; Vercel
deploymentEnabled is false for main, preview and wildcard. No CI dispatch or PR
creation is included in that zero-run screen. A later PR/merge must account for
its broader CI matrix and preserve the separate release HOLD.

Before installation, source rollback is a normal revert of this candidate commit
on a reviewed branch; production is unchanged. SQL65 is transaction-contained.
After any real use or uncertain outcome, retain its terminal columns/receipts,
disable the exact caller under an approved drain and reconcile; never drop or
restore over used/UNKNOWN records. A rollback that removes the lifecycle fix must
also keep the native lane disabled. No automatic DB rollback is provided.
