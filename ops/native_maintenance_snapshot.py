"""Private offline checkpoints of a bound journal pair; never a mutation permit.

Both source journals must be closed. This module takes both exclusive locks.
Payloads contain confidential HOLD data. Do not log/publish them. A digest proves
bytes, not freshness or authority: restoration still needs independent recovery.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ops.native_maintenance_workflow_pause import Journal, digest, encoded, require, unique, validate_plan

MAX_BYTES = 16 * 1024 * 1024
NAMES = ('operation', 'pause')


def _hex(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value)


def _pair(records):
    operation, pause = (records[n] for n in NAMES)
    require(operation and pause, 'SNAPSHOT_UNBOUND')
    first = operation[0]['event']
    require(type(first) is dict and set(first) == {'kind', 'scope'}
            and first['kind'] == 'BOUND' and type(first['scope']) is dict, 'SNAPSHOT_BOUND')
    scope = first['scope']
    binding = digest(scope)
    plan_event = pause[0]['event']
    require(type(plan_event) is dict and set(plan_event) ==
            {'kind', 'digest', 'plan', 'operation_scope_digest'}
            and plan_event['kind'] == 'PLAN'
            and plan_event['operation_scope_digest'] == binding, 'SNAPSHOT_PAIR_BINDING')
    plan = plan_event['plan']
    validate_plan(plan)
    require(plan_event['digest'] == digest(plan) == scope.get('workflow_plan_digest')
            and plan['source'] == scope.get('source'), 'SNAPSHOT_PLAN_BINDING')
    # Semantic replay is performed by the executor during separately reviewed recovery.
    return binding


def _parse(data):
    require(type(data) is bytes and 0 < len(data) <= MAX_BYTES, 'SNAPSHOT_SIZE')
    value = json.loads(data, object_pairs_hook=unique)
    require(type(value) is dict and set(value) == {'version', 'scope_digest', 'journals'}
            and type(value['version']) is int and value['version'] == 1
            and _hex(value['scope_digest']) and encoded(value) == data, 'SNAPSHOT_FORMAT')
    rows = value['journals']
    require(type(rows) is dict and set(rows) == set(NAMES), 'SNAPSHOT_JOURNALS')
    records = {}
    for name in NAMES:
        require(type(rows[name]) is list and 0 < len(rows[name]) <= 1024, 'SNAPSHOT_COUNT')
        records[name] = []
        previous = None
        total_bytes = 0
        for raw in rows[name]:
            require(type(raw) is str and len(raw.encode()) <= 262144, 'SNAPSHOT_RECORD_SIZE')
            total_bytes += len(raw.encode())
            require(total_bytes <= MAX_BYTES // 4, 'SNAPSHOT_JOURNAL_BYTES')
            record = json.loads(raw, object_pairs_hook=unique)
            require(type(record) is dict and set(record) == {'previous', 'event'}
                    and record['previous'] == previous and encoded(record).decode() == raw,
                    'SNAPSHOT_RECORD_CHAIN')
            previous = digest(record)
            records[name].append(record)
    require(_pair(records) == value['scope_digest'], 'SNAPSHOT_SCOPE')
    return value


def _read(journal):
    journal.assert_live()
    names = ['lock'] + [f'{i:06d}.json' for i in range(len(journal.records))]
    require(sorted(os.listdir(journal.fd)) == sorted(names), 'SNAPSHOT_FILES_CHANGED')
    rows = []
    for index, record in enumerate(journal.records):
        fd = os.open(f'{index:06d}.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=journal.fd)
        try:
            Journal._regular(fd)
            raw = os.read(fd, 262145)
        finally:
            os.close(fd)
        require(raw == encoded(record), 'SNAPSHOT_RECORD_CHANGED')
        rows.append(raw.decode())
    journal.assert_live()
    return rows


def capture(operation_path, pause_path):
    """Return bounded private bytes while holding both exclusive Journal locks.

No snapshot is possible during an active executor. Trusted storage administrators
must remain coordinated; advisory locks do not exclude a hostile privileged writer.
"""
    with Journal(operation_path, max_bytes=MAX_BYTES // 4) as operation, \
            Journal(pause_path, max_bytes=MAX_BYTES // 4) as pause:
        return capture_locked(operation, pause)


def capture_locked(operation, pause):
    """Capture already-owned exclusive journals synchronously; never unlock them.

The caller must serialize appends for the entire capture and remote acceptance.
This is not a snapshot of arbitrary paths or a way around another owner's lock.
"""
    require(type(operation) is Journal and type(pause) is Journal, 'SNAPSHOT_JOURNAL_OWNER')
    operation.assert_live()
    pause.assert_live()
    require((os.fstat(operation.fd).st_dev, os.fstat(operation.fd).st_ino) !=
            (os.fstat(pause.fd).st_dev, os.fstat(pause.fd).st_ino), 'SNAPSHOT_SEPARATE_JOURNALS')
    rows = {name: _read(journal) for name, journal in zip(NAMES, (operation, pause))}
    binding = _pair({name: journal.records for name, journal in zip(NAMES, (operation, pause))})
    data = encoded({'version': 1, 'scope_digest': binding, 'journals': rows})
    _parse(data)
    require(rows == {name: _read(journal) for name, journal in zip(NAMES, (operation, pause))},
            'SNAPSHOT_CHANGED_DURING_CAPTURE')
    return data


def _write(directory, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)


def restore(data, expected_digest, expected_scope, parent):
    """Restore into a NEW digest-named directory below an existing private parent.

Expected digest/scope must come from independently accepted recovery evidence,
not be inferred from the downloaded object. No overwrite, deletion, activation,
latest-version selection or automatic cleanup. Interrupted output blocks retry.
"""
    require(_hex(expected_digest) and _hex(expected_scope)
            and type(data) is bytes and len(data) <= MAX_BYTES
            and hashlib.sha256(data).hexdigest() == expected_digest, 'SNAPSHOT_DIGEST')
    value = _parse(data)
    require(value['scope_digest'] == expected_scope, 'SNAPSHOT_EXPECTED_SCOPE')
    parent = Path(parent)
    require(parent.absolute() == parent.resolve(), 'SNAPSHOT_PARENT_LINK')
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    root_fd = None
    try:
        info = os.fstat(parent_fd)
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                'SNAPSHOT_PRIVATE_PARENT')
        os.mkdir(expected_digest, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        root_fd = os.open(expected_digest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        root = parent / expected_digest
        for name in NAMES:
            os.mkdir(name, mode=0o700, dir_fd=root_fd)
            os.fsync(root_fd)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                for index, raw in enumerate(value['journals'][name]):
                    _write(child, f'{index:06d}.json', raw.encode())
            finally:
                os.close(child)
        require(capture(root / 'operation', root / 'pause') == data, 'SNAPSHOT_RESTORE_VERIFY')
        # Persist lock entries created by the verification reopen as well.
        for name in NAMES:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
            try:
                os.fsync(child)
            finally:
                os.close(child)
        _write(root_fd, 'RECOVERY_ONLY', encoded({'archive_digest': expected_digest,
                                                'scope_digest': expected_scope,
                                                'requires_independent_reconciliation': True}))
        require(os.stat(parent, follow_symlinks=False).st_ino == info.st_ino
                and os.stat(parent, follow_symlinks=False).st_dev == info.st_dev,
                'SNAPSHOT_PARENT_CHANGED')
        return root
    finally:
        if root_fd is not None:
            os.close(root_fd)
        os.close(parent_fd)
