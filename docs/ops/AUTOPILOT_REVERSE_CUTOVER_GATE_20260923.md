# Autopilot Oracle → Neon reverse cutover gate

Status: **STOP after the first Oracle write**. This is a recovery contract, not an authorization to switch. The observed production route is Neon epoch 0; no Oracle production/shadow promotion or reverse rehearsal has been completed.

## Boundary

The route lease in `ops/github_autopilot_db_route.py` coordinates a subset of GitHub consumers. It does not prove that every resident service, historical workflow, owner credential, manual runner, or shadow writer is stopped. A route file alone cannot establish that the old source is fenced. The route installer in `ops/oracle_light_route_install.py` is explicitly `pre_cutover_only` and supplies no reverse operation.

Before promoting either contour, create a source recovery point outside Light Oracle and verify an isolated restore. Record immutable identifiers for the held Neon production and shadow snapshots, selected `public.schema_migration` keys/checksums, target restore manifests, source fence evidence, route epoch, client inventory, and exact permitted writer set. The shared migration ledger must not be copied wholesale: it also contains school migrations. Keep the contours separate.

## Failure before the first Oracle-authoritative application write

The initial restore necessarily commits writes on Oracle. Its verified target
manifest is the import baseline; “first write” below means the first committed
application, maintenance or externally visible Autopilot change **after** that
baseline becomes authoritative, in either production or shadow. Import writes
cannot be mistaken for post-promotion changes.

Do not infer “no writes” from empty task or outbox counts. DELETE, DDL, sequence advancement, and a temporarily connected legacy writer can erase or evade that observation. A return to Neon is eligible only with independently reviewed proof that **both** Oracle contours received no committed writes after their verified final snapshots. The proof must cover all write paths, including owner/DDL, background jobs, GitHub callbacks and shadow; a route lease or application audit alone is insufficient. With incomplete proof, apply the post-write rule below.

Under the protected administrator lock: pause admissions; stop and fence every Oracle writer; verify the held Neon snapshots and Neon fence state; review immutable evidence of zero Oracle changes; set the route and all client DSNs together to a new epoch; enable exactly one writer; prove a genuine task/ACK/terminal chain; then release remaining clients. A failed verification leaves both sides fenced and the route paused.

## Failure after any Oracle write, or when write history is unknown

**No direct route flip to Neon.** Keep both source and target write-fenced while producing a recovery plan from a consistent Oracle snapshot. The plan must identify every Oracle-only change since the final Neon snapshot, including rows subsequently deleted, DDL, role/ACL/RLS changes, migration ledger, sequence values, shadow changes, and external effects such as GitHub commands and callback receipts. Row counts and current-state hashes cannot by themselves reconstruct deleted history or undo an external action.

Reconcile into an isolated Neon recovery target first. Review object-level differences, principal permissions, idempotency keys, dispatch/receipt relationships, sequence state, and any conflicting writes. Preserve immutable off-host copies of both sides. An independent reviewer must verify the recovery procedure and a replayed end-to-end task before production Neon is considered. If capture is incomplete or an external effect cannot be reconciled, restore Oracle service through a reviewed forward repair rather than asserting a safe reverse.

## Implementable prerequisites for an actual reverse procedure

1. Install a write-capture or journal contract **before** Oracle promotion that covers every mutable Autopilot object and DDL/owner path in both contours. Verify coverage under the exact production roles and workloads. PostgreSQL row triggers alone do not capture TRUNCATE, DDL, sequence increments, or external GitHub effects.
2. Pin a common recovery point and transaction ordering across the final Neon snapshot, Oracle import, and journal activation. Prove no gap between snapshot and first Oracle write.
3. Build a deterministic, idempotent Oracle → isolated Neon replay with conflict detection and a separately reviewed manifest. Never replay a terminal or owner command as a new external dispatch.
4. Exercise a failure **after** an Oracle canary write, reconcile it in isolation, compare both contours and externally visible receipts, and test rollback of the reversal. Only then define a protected reverse workflow with a unique route epoch and a fail-closed lock.

Until these prerequisites exist, the operational response to a post-write Oracle failure is to fence writes, retain all recovery objects, investigate, and repair Oracle. No draft PR, pasted PASS receipt, or route metadata substitutes for the proof above.
