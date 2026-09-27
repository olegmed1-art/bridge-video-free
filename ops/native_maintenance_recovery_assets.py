"""Immutable private source/manifest recovery material, never a mutation permit.

The trusted recovery controller must independently accept all five identifiers.
This format preserves candidate manifests as well; storing one does not approve
its baseline or authorize SQL. Semantic reconciliation remains the permission
engine's responsibility under the retained, independently reviewed source.
"""
import base64
import json
import os
from pathlib import Path
import stat

from ops import native_maintenance_bundle as bundle
from ops.native_permission_hold_guard import EXPECTED_TARGET

MAX_MANIFEST = 4 * 1024 * 1024
MAX_BYTES = 9 * 1024 * 1024
FILES = ('source.json', 'manifest.json')


def binding(source, source_digest, manifest_digest, baseline_digest):
    bundle.check(bundle.identifier(source, 40) and bundle.identifier(source_digest, 64)
                 and bundle.identifier(manifest_digest, 64) and bundle.identifier(baseline_digest, 64),
                 'ASSETS_IDENTIFIERS')
    return dict(version=1, kind='NATIVE_RECOVERY_ASSETS', source=source,
                source_digest=source_digest, manifest_digest=manifest_digest,
                baseline_digest=baseline_digest, target=EXPECTED_TARGET)


def manifest(data, expected_digest, baseline_digest):
    from database import native_cli_permission_engine as engine
    bundle.check(type(data) is bytes and 0 < len(data) <= MAX_MANIFEST
                 and bundle.digest(data) == expected_digest, 'ASSETS_MANIFEST_DIGEST')
    value = json.loads(data, object_pairs_hook=bundle.unique)
    bundle.check(type(value) is dict and set(value) == {'version', 'target', 'before', 'after'}
                 and type(value['version']) is int and value['version'] == 1
                 and all(type(value[k]) is dict for k in ('target', 'before', 'after'))
                 and bundle.canonical(value) == data, 'ASSETS_MANIFEST_FORMAT')
    bundle.check(value['target'] == EXPECTED_TARGET and value['before'].get('target') == EXPECTED_TARGET
                 and engine.digest(value['before']) == baseline_digest, 'ASSETS_MANIFEST_TARGET_OR_BASELINE')
    engine.dormant(value['before'])
    bundle.check(value['after'] == engine.expected_after(value['before']), 'ASSETS_MANIFEST_DELTA')
    return value


def build(source_data, manifest_data, source, source_digest, manifest_digest, baseline_digest):
    identity = binding(source, source_digest, manifest_digest, baseline_digest)
    bundle.decode(source_data, source, source_digest)
    manifest(manifest_data, manifest_digest, baseline_digest)
    data = bundle.canonical(dict(binding=identity,
        source=base64.b64encode(source_data).decode('ascii'),
        manifest=base64.b64encode(manifest_data).decode('ascii')))
    bundle.check(len(data) <= MAX_BYTES, 'ASSETS_SIZE')
    return data


def decode(data, expected_digest, source, source_digest, manifest_digest, baseline_digest):
    identity = binding(source, source_digest, manifest_digest, baseline_digest)
    bundle.check(type(data) is bytes and 0 < len(data) <= MAX_BYTES
                 and bundle.identifier(expected_digest, 64)
                 and bundle.digest(data) == expected_digest, 'ASSETS_DIGEST')
    value = json.loads(data, object_pairs_hook=bundle.unique)
    bundle.check(type(value) is dict and set(value) == {'binding', 'source', 'manifest'}
                 and value['binding'] == identity and bundle.canonical(value) == data,
                 'ASSETS_BINDING')
    parts = []
    for name, limit in (('source', bundle.MAX_WIRE), ('manifest', MAX_MANIFEST)):
        encoded = value[name]
        bundle.check(type(encoded) is str and 0 < len(encoded) <= 4 * ((limit + 2) // 3),
                     'ASSETS_ENCODED_SIZE')
        raw = base64.b64decode(encoded, validate=True)
        bundle.check(base64.b64encode(raw).decode('ascii') == encoded and 0 < len(raw) <= limit,
                     'ASSETS_ENCODING')
        parts.append(raw)
    bundle.decode(parts[0], source, source_digest)
    manifest(parts[1], manifest_digest, baseline_digest)
    return tuple(parts)


def restore(data, expected_digest, source, source_digest, manifest_digest, baseline_digest, parent):
    """Create new private fsynced material; preserve partial output after failure.

No code is imported/executed from the package. No journal/latest selection, SQL,
overwrite, repair or cleanup. The caller retains a trusted decoder separately.
"""
    parts = decode(data, expected_digest, source, source_digest, manifest_digest, baseline_digest)
    parent = Path(parent)
    bundle.check(parent.is_absolute() and parent == parent.resolve(), 'ASSETS_PARENT_LINK')
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    root_fd = None
    try:
        info = os.fstat(parent_fd)
        bundle.check(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700,
                     'ASSETS_PRIVATE_PARENT')
        os.mkdir(expected_digest, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
        root_fd = os.open(expected_digest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                          dir_fd=parent_fd)
        for name, raw in zip(FILES, parts):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=root_fd)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(root_fd)
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            with os.fdopen(fd, 'rb') as stream:
                actual = os.fstat(stream.fileno())
                bundle.check(stat.S_ISREG(actual.st_mode) and actual.st_nlink == 1
                             and actual.st_uid == os.getuid()
                             and stat.S_IMODE(actual.st_mode) == 0o600
                             and stream.read(len(raw) + 1) == raw, 'ASSETS_RESTORE_READBACK')
        current = os.stat(parent, follow_symlinks=False)
        child = os.stat(expected_digest, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(root_fd)
        bundle.check((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino)
                     and stat.S_ISDIR(child.st_mode)
                     and (child.st_dev, child.st_ino) == (opened.st_dev, opened.st_ino),
                     'ASSETS_RESTORE_DIRECTORY_CHANGED')
        return parent / expected_digest
    finally:
        if root_fd is not None:
            os.close(root_fd)
        os.close(parent_fd)


def retain(store, data, expected_digest, source, source_digest, manifest_digest, baseline_digest):
    """Create-only publication and exact download verification; no approval."""
    decode(data, expected_digest, source, source_digest, manifest_digest, baseline_digest)
    store.put_assets(data, expected_digest, source, source_digest, manifest_digest, baseline_digest)
    restored = store.read_assets(expected_digest, source, source_digest, manifest_digest, baseline_digest)
    bundle.check(restored == data, 'ASSETS_REMOTE_READBACK')
    decode(restored, expected_digest, source, source_digest, manifest_digest, baseline_digest)
    return expected_digest
