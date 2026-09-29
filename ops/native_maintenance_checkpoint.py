"""Synchronous journal-pair publication protocol, not a production store/runtime.

Store implementations must supply private off-VM immutable objects and a strongly
consistent conditional head update. There is no local-disk or permissive default.
Payloads contain private HOLD data: never log them or put them in public artifacts.
"""
import hashlib
import json

from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import encoded, require, unique


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_head(data, scope):
    require(type(data) is bytes and 0 < len(data) <= 4096, 'CHECKPOINT_HEAD_SIZE')
    value = json.loads(data, object_pairs_hook=unique)
    require(type(value) is dict and set(value) ==
            {'version', 'scope_digest', 'sequence', 'previous', 'archive_digest'}
            and type(value['version']) is int and value['version'] == 1
            and value['scope_digest'] == scope and snapshot._hex(scope)
            and type(value['sequence']) is int and 1 <= value['sequence'] <= 4096
            and snapshot._hex(value['archive_digest'])
            and (value['previous'] is None if value['sequence'] == 1
                 else snapshot._hex(value['previous']))
            and encoded(value) == data, 'CHECKPOINT_HEAD_INVALID')
    return value


def read_head(store, scope):
    result = store.read_head(scope)
    require(result is None or (type(result) is tuple and len(result) == 2
            and type(result[1]) is str and 0 < len(result[1]) <= 1024), 'CHECKPOINT_HEAD_RESPONSE')
    if result is not None:
        parse_head(result[0], scope)
    return result


def read_archive(store, scope, archive_digest):
    data = store.read_archive(scope, archive_digest, snapshot.MAX_BYTES)
    require(type(data) is bytes and 0 < len(data) <= snapshot.MAX_BYTES
            and sha(data) == archive_digest, 'CHECKPOINT_ARCHIVE_DIGEST')
    value = snapshot._parse(data)
    require(value['scope_digest'] == scope, 'CHECKPOINT_ARCHIVE_SCOPE')
    return data


def accepted_latest(store, scope, expected_head_digest):
    """Read a separately accepted current head; never silently accept a stale copy.

Returns private bytes for independent recovery. Does not replay or authorize SQL.
The head digest must be independently accepted, not obtained from this payload.
"""
    require(snapshot._hex(scope) and snapshot._hex(expected_head_digest), 'CHECKPOINT_ACCEPTANCE')
    store.assert_private()
    head = read_head(store, scope)
    require(head is not None and sha(head[0]) == expected_head_digest, 'CHECKPOINT_LATEST_CHANGED')
    value = parse_head(head[0], scope)
    data = read_archive(store, scope, value['archive_digest'])
    require(read_head(store, scope) == head, 'CHECKPOINT_LATEST_CHANGED')
    store.assert_private()
    return data


class JournalCheckpoint:
    """Append-only pair snapshots followed by conditional publication of latest.

Store methods: assert_private(), read_head(scope)->None|(bytes, revision),
put_archive(scope, sha256, bytes) with create-only semantics,
read_archive(scope, sha256, limit)->bytes, and
compare_head(scope, expected_revision_or_None, bytes) with CAS semantics.
Any ambiguous reply poisons this instance. No write retries or automatic repair.
Existing scope requires a separately accepted head digest when constructing a new
instance. A stale local journal pair may never replace an accepted newer prefix.
"""
    def __init__(self, store, *, accepted_head_digest=None):
        require(all(callable(getattr(store, name, None)) for name in
                    ('assert_private', 'read_head', 'put_archive', 'read_archive', 'compare_head')),
                'CHECKPOINT_STORE_REQUIRED')
        require(accepted_head_digest is None or snapshot._hex(accepted_head_digest), 'CHECKPOINT_ACCEPTANCE')
        self.store = store
        self.accepted = accepted_head_digest
        self.scope = self.head = self.archive = None
        self.initialized = self.failed = False

    def accept_resume(self, scope, operation, pause):
        """Read-only acceptance of an exact pair at a cross-run handoff.

        No local suffix, stale prefix, inferred head or already-used checkpoint
        object may authorize a fresh staged dispatch. sync() still uses CAS.
        """
        require(not self.failed, 'CHECKPOINT_ALREADY_FAILED')
        try:
            require(not self.initialized and self.accepted is not None, 'CHECKPOINT_RESUME_ACCEPTANCE')
            data = accepted_latest(self.store, scope, self.accepted)
            require(snapshot.capture_locked(operation, pause) == data, 'CHECKPOINT_RESUME_PAIR_CHANGED')
        except BaseException:
            self.failed = True
            raise


    def sync(self, scope, operation, pause):
        require(not self.failed, 'CHECKPOINT_ALREADY_FAILED')
        try:
            data = snapshot.capture_locked(operation, pause)
            value = snapshot._parse(data)
            require(value['scope_digest'] == scope and (self.scope is None or self.scope == scope),
                    'CHECKPOINT_SCOPE_CHANGED')
            self.store.assert_private()
            remote = read_head(self.store, scope)
            if not self.initialized:
                require((remote is None and self.accepted is None) or
                        (remote is not None and self.accepted == sha(remote[0])),
                        'CHECKPOINT_EXISTING_SCOPE_REQUIRES_ACCEPTANCE')
                if remote is None:
                    require(all(len(value['journals'][name]) == 1 for name in snapshot.NAMES),
                            'CHECKPOINT_FRESH_BOUND_PAIR_REQUIRED')
                self.head = remote
                self.scope = scope
                if remote is not None:
                    self.archive = read_archive(self.store, scope, parse_head(remote[0], scope)['archive_digest'])
                self.initialized = True
            require(remote == self.head, 'CHECKPOINT_CONCURRENT_CHANGE')
            if self.archive is not None:
                previous = snapshot._parse(self.archive)
                for name in snapshot.NAMES:
                    before, after = previous['journals'][name], value['journals'][name]
                    require(after[:len(before)] == before, 'CHECKPOINT_JOURNAL_ROLLBACK_OR_FORK')
            if self.archive != data:
                archive_digest = sha(data)
                self.store.put_archive(scope, archive_digest, data)
                require(read_archive(self.store, scope, archive_digest) == data, 'CHECKPOINT_READBACK')
                sequence = 1 if self.head is None else parse_head(self.head[0], scope)['sequence'] + 1
                new_head = encoded(dict(version=1, scope_digest=scope, sequence=sequence,
                                       previous=None if self.head is None else sha(self.head[0]),
                                       archive_digest=archive_digest))
                parse_head(new_head, scope)
                # No advance until the complete archive has been verified.
                self.store.compare_head(scope, None if self.head is None else self.head[1], new_head)
                confirmed = read_head(self.store, scope)
                require(confirmed is not None and confirmed[0] == new_head, 'CHECKPOINT_HEAD_ACK_UNKNOWN')
                self.head, self.archive = confirmed, data
            else:
                require(read_archive(self.store, scope, sha(data)) == data, 'CHECKPOINT_READBACK')
            self.store.assert_private()
            require(read_head(self.store, scope) == self.head
                    and snapshot.capture_locked(operation, pause) == data, 'CHECKPOINT_CHANGED_DURING_SYNC')
            return sha(self.head[0])
        except BaseException:
            self.failed = True
            raise

def publish_reconciled_session_suffix(store, scope, operation, pause, *,
                                      accepted_head_digest, accepted_pair_digest,
                                      observed_outcome, reconciler):
    """One separately accepted, consumed-session journal recovery publication.

    This is not an execute/resume admission. The caller must independently accept
    BOTH digests and supply a live reconciler that observes the database outcome,
    prior process/backend drain and HOLD. A failed/ambiguous publication is never
    retried in this invocation; inspect and accept the new remote head separately.
    Pause/restore suffixes and lost local journals need a different recovery path.
    """
    require(snapshot._hex(scope) and snapshot._hex(accepted_head_digest)
            and snapshot._hex(accepted_pair_digest)
            and observed_outcome in ('BEFORE', 'AFTER')
            and callable(getattr(reconciler, 'assert_reconciled', None)),
            'CHECKPOINT_RECOVERY_ACCEPTANCE')
    data = snapshot.capture_locked(operation, pause)
    require(sha(data) == accepted_pair_digest, 'CHECKPOINT_RECOVERY_PAIR_CHANGED')
    local = snapshot._parse(data)
    require(local['scope_digest'] == scope, 'CHECKPOINT_RECOVERY_SCOPE')
    remote_data = accepted_latest(store, scope, accepted_head_digest)
    remote = snapshot._parse(remote_data)
    old_op, new_op = remote['journals']['operation'], local['journals']['operation']
    require(len(new_op) > len(old_op) and new_op[:len(old_op)] == old_op
            and new_op != old_op and local['journals']['pause'] == remote['journals']['pause'],
            'CHECKPOINT_RECOVERY_SESSION_SUFFIX_REQUIRED')
    # The session opportunity has been consumed. Only its existing records may
    # be advanced; publishing an uncheckpointed pause/restore effect needs a
    # separate, independently designed reconciliation protocol.
    events = [json.loads(row, object_pairs_hook=unique)['event'] for row in new_op]
    kinds = [event.get('kind') for event in events]
    require(kinds[:2] == ['BOUND', 'PREPARED']
            and kinds[2:] in (['SESSION_BOUND'],
                              ['SESSION_BOUND', 'SESSION_INTENT'],
                              ['SESSION_BOUND', 'SESSION_INTENT', 'SESSION_RESULT'],
                              ['SESSION_BOUND', 'SESSION_INTENT', 'SESSION_ERROR'])
            and len(old_op) >= 2
            and events[0]['scope'].get('execution_mode') == 'staged_v1'
            and events[0]['scope'].get('operation') in ('apply', 'rollback')
            and (kinds[-1] != 'SESSION_RESULT' or
                 events[-1].get('outcome') == observed_outcome ==
                 ('BEFORE' if events[0]['scope']['operation'] == 'rollback' else 'AFTER')),
            'CHECKPOINT_RECOVERY_NOT_CONSUMED_SESSION')
    # This contract is furnished by the trusted recovery controller, never by
    # the failed executor or an auto-selected checkpoint. Recheck after the CAS.
    reconciler.assert_reconciled(scope, observed_outcome)
    checkpoint = JournalCheckpoint(store, accepted_head_digest=accepted_head_digest)
    result = checkpoint.sync(scope, operation, pause)
    reconciler.assert_reconciled(scope, observed_outcome)
    require(sha(snapshot.capture_locked(operation, pause)) == accepted_pair_digest,
            'CHECKPOINT_RECOVERY_PAIR_CHANGED')
    return result
