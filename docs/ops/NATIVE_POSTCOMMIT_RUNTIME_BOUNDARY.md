# Native recipient behavior after grant COMMIT

2026-09-26; ASSURED disposable evidence following PR #1976.

The critical window continues after GRANT commits. The six function catalog
updates are no longer protected by that transaction, even while the separate
ordinary-autopilot-table fence remains held.

`native_cli_postcommit_runtime_rehearsal.py` extends the existing PostgreSQL 18
permission-engine fixture. It uses the real six-grant engine with an explicit
CI maintenance stub. Production apply remains unavailable.

## Bounded dormant checks

After COMMIT, six fresh connections set both session and current identity to
the non-superuser recipient. Sessions are writable so read-only enforcement
cannot explain refusal. Each native RPC receives a well-typed request with a
nonexistent dispatch UUID, under disabled config and zero receipts/active tasks.
Five exact application errors and `current=false` are required. Permission,
timeout and unrelated errors do not count as success. All ordinary autopilot
table rows and the full engine metadata snapshot must remain unchanged.

This covers these six inputs only. The existing `native_cli_runtime_acl.sql`
separately tests disabled reserve with a valid published dispatch. Neither row
equality nor these cases prove all-input dependency safety, absence of sequence
changes, or absence of external/transient effects.

## Transient catalog counterexample

With the same fence still held, a privileged disposable contender commits a
replacement snapshot function. Its SECURITY DEFINER body writes one marker to
a new disposable schema outside autopilot. The recipient receives no extra
privileges; a fresh recipient call commits the marker. The contender then
commits restoration of the complete original function definition.

A fresh observer must see both `inspect == AFTER` and the retained marker.
Ordinary autopilot rows remain unchanged. This proves that later metadata
equality cannot establish that no unsafe call happened during a transient
post-COMMIT change. It does not claim that the original function has this effect.

Cleanup restores the saved function on failure, removes only fixture-created
objects, revokes the six grants through the existing drift-refusing engine,
and verifies BEFORE. No production RPC, grant, configuration change, workflow
disablement or host operation is part of this test. Code rollback is a revert.

Expected CI markers:

- `NATIVE_POSTCOMMIT_EMPTY_RECEIPT_CALLS_PASS`
- `NATIVE_POSTCOMMIT_RESTORED_CATALOG_EFFECT_CONFIRMED`

## Narrow next implementation

Retain full snapshots and the refusing default guard. The intended coordinator
holds existing GitHub mutation groups, the actual route lease exclusively and
the DB fence through final fresh inspection. Groups/lease do not exclude direct
owner operations or ungrouped critical migrations: those require a scoped,
explicitly coordinated owner window. Relevant scope is native code/dependencies,
effective recipient authority and admission state, not every backup reader or
unrelated role. A coordinator rehearsal must prove loss/outcome handling before
production wiring. This test supplies evidence, not that coordination agreement.
