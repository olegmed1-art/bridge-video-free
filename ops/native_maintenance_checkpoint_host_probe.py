"""Fixed synthetic host journal probe. No owner SQL or permission session."""
from contextlib import contextmanager
import fcntl
import os
import time

from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_checkpoint_transport as rpc
from ops import native_maintenance_snapshot as snapshot
from ops import native_maintenance_store as storage
from ops.native_maintenance_workflow_pause import Journal, digest, require
from ops.oracle_light_active_hold_attest import attest


def identity(source, run_id, attempt):
    from ops.native_maintenance_bundle import identifier
    require(identifier(source, 40) and type(run_id) is int and run_id > 0
            and type(attempt) is int and attempt > 0, 'DUPLEX_PROBE_IDENTITY')
    plan = dict(version=1, repository='olegmed1-art/bridge-video-free', source=source,
                workflows=[dict(id=1, path='.github/workflows/ci-only-synthetic.yml', state='active',
                                updated_at='2026-09-26T00:00:00Z')])
    scope = dict(source=source, workflow_plan_digest=digest(plan),
                 manifest_digest=digest({'probe': 'SYNTHETIC_ONLY_NOT_A_PERMISSION_MANIFEST'}),
                 probe='SUPERVISED_DUPLEX_SYNTHETIC_CHECKPOINT', run_id=run_id, attempt=attempt)
    scope_digest = digest(scope)
    binding = digest(dict(version=1, source=source, run_id=run_id, attempt=attempt,
                          scope_digest=scope_digest, purpose='SYNTHETIC_CHECKPOINT_ONLY'))
    return plan, scope, scope_digest, binding


@contextmanager
def journals(scope):
    """Create one retained synthetic scope in the already prepared persistent store."""
    require(snapshot._hex(scope) and os.getuid() == 0, 'DUPLEX_PROBE_SCOPE')
    storage.trusted_parent(storage.PARENT)
    root = storage.PARENT / storage.NAME
    before = storage.private_directory(root)
    storage.persistent_mount(root)
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = None
    try:
        require((before.st_dev, before.st_ino) == (os.fstat(directory).st_dev, os.fstat(directory).st_ino),
                'DUPLEX_STORE_CHANGED')
        lock = storage.open_file(directory, 'lock', os.O_RDWR)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        marker = storage.open_file(directory, 'VERSION', os.O_RDONLY)
        try:
            require(os.read(marker, 256) == storage.VERSION, 'DUPLEX_STORE_VERSION')
        finally:
            os.close(marker)
        stats = os.fstatvfs(directory)
        require(stats.f_bavail * stats.f_frsize > 1024 * 1024, 'DUPLEX_STORE_SPACE')
        os.mkdir(scope, mode=0o700, dir_fd=directory)
        os.fsync(directory)
        base = root / scope
        descriptor = os.open(scope, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            for name in (*snapshot.NAMES, 'restored'):
                os.mkdir(name, mode=0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            with Journal(base / 'operation') as operation, Journal(base / 'pause') as pause:
                yield operation, pause, base / 'restored'
        finally:
            os.close(descriptor)
        after = storage.private_directory(root)
        require((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino), 'DUPLEX_STORE_CHANGED')
    finally:
        if lock is not None:
            os.close(lock)
        os.close(directory)


def main(source, run_id, attempt):
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'DUPLEX_HOST')
    plan, scope, scope_digest, binding = identity(source, run_id, attempt)
    started = time.monotonic()
    before = attest()
    channel = rpc.Channel(0, 1, binding, seconds=60)
    store = rpc.ProxyStore(channel, scope_digest)
    with journals(scope_digest) as (operation, pause, parent):
        operation.append(dict(kind='BOUND', scope=scope))
        pause.append(dict(kind='PLAN', plan=plan, digest=digest(plan), operation_scope_digest=scope_digest))
        protocol = checkpoint.JournalCheckpoint(store)
        first = protocol.sync(scope_digest, operation, pause)
        operation.append(dict(kind='SYNTHETIC_INTENT', outcome='UNKNOWN'))
        latest = protocol.sync(scope_digest, operation, pause)
        require(first != latest, 'DUPLEX_HEAD_NOT_ADVANCED')
        data = checkpoint.accepted_latest(store, scope_digest, latest)
        restored = snapshot.restore(data, checkpoint.sha(data), scope_digest, parent)
        require(snapshot.capture(restored / 'operation', restored / 'pause') == data
                and snapshot.capture_locked(operation, pause) == data, 'DUPLEX_RESTORE')
    require(attest() == before, 'DUPLEX_HOLD_CHANGED')
    channel.send(dict(kind='SYNTHETIC_DUPLEX_COMPLETE', binding=binding, scope=scope_digest,
                      source=source, head_digest=latest, sequence=store.sequence,
                      elapsed_ms=int((time.monotonic() - started) * 1000), hold_unchanged=True))
