# Synchronous intent checkpoint protocol (dormant)

2026-09-26. ASSURED / I2 review before merge. No production activation.

Offline copying after journal closure leaves a host-loss interval between a
remote mutation and backup. The executor now requires a checkpoint dependency:
it synchronously publishes the pristine BOUND/PLAN pair, every operation event,
and the current pair immediately before each workflow PUT. SESSION_INTENT is
therefore accepted before the SQL session; ENABLE_INTENT precedes enable. The
run/operator/lifetime guards are checked again after storage latency. There is
no default checkpoint, automatic workflow enable, or SQL replay.

`snapshot.capture_locked` reads the executor's already-owned Journal instances
without releasing or reacquiring either exclusive lock. Both on-disk chains,
private inode identities, pair binding and a second capture are checked. The
caller must serialize all appends. Privileged storage writers remain subject to
the independently established coordination window.

`JournalCheckpoint` implements this publication order:

1. Verify private storage and the exact expected current head/revision.
2. Verify both local journals extend the previously accepted pair.
3. Create the immutable content-addressed archive and verify full readback.
4. Conditionally publish a monotonic head with the previous head digest.
5. Read back that head, recheck privacy and re-capture the unchanged local pair.

Any exception or uncertain reply poisons the instance. No mutation may follow an
unacknowledged checkpoint, even if the remote write actually succeeded. An
archive left before head publication is retained, never treated as accepted by
listing or timestamp. A missing response is not a rollback. An action whose
reply was lost remains represented by its earlier intent and needs independent
GitHub/DB reconciliation.

An absent head is allowed only for the pristine BOUND/PLAN pair. Existing history
may not initialize a missing head. A new instance using an existing scope must
receive an independently accepted current head digest; it cannot infer approval
from the downloaded object. `accepted_latest` reads exactly that head and archive
with a second head observation, and returns private bytes for the existing
recovery-only snapshot restore. Stale head acceptance, journal rollback/fork,
concurrent publication and corrupt archives fail closed.

## Remaining production assembly

This PR supplies the protocol, not its off-VM adapter. A real implementation must
provide strong read-after-write consistency, create-only archives, conditional
head writes with genuine revision tokens, no silent retries, bounded reads,
private access and verified budget/retention. A missing/deleted head must not be
mistaken for a never-used scope: production needs a trusted fresh-operation
registration and retained recovery index, including detection of deleted heads.
Store method names or a callable `sync` do not prove any of these properties.

The archive preserves the bound source/manifest/HOLD identifiers and the pair;
the separately accepted manifest bytes and exact source bundle must also be
retained privately before production dispatch. Latest-head acceptance, provider
administration exclusion, credential delivery, owner-capable host runtime,
live timing and one-task admission are still separate requirements. No OCI
adapter, secret transfer, GRANT/REVOKE, HOLD release or pilot is enabled here.

## Evidence and rollback

Unit faults cover archive/head writes before and after a lost reply, failed
readback, corrupt bytes, stale local history, CAS conflict, privacy loss, local
change during upload and lifetime expiry during persistence. Integration tests
observe accepted DISABLE_INTENT, SESSION_INTENT and ENABLE_INTENT before the
corresponding simulated remote action and prove lost acceptance blocks dispatch.
The PG18 fixture uses the real checkpoint protocol over explicit memory storage,
checks a remotely accepted intent before actual SQL, and restores the accepted
pair after a real commit with a lost return. It is not an OCI durability proof.

Source rollback is a revert before activation. Once any production intent exists,
preserve journals and remote objects; a source revert cannot reconcile its effect.
Recovery requires the exact accepted head and independent state observation.
