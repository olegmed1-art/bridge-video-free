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
