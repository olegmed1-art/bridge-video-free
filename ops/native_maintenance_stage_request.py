"""Launcher request provenance and durable single-use admission.

File presence is not approval. accepted_digest is supplied by the authenticated
owner/manual workflow, outside these bytes. No helper selects an observed head,
adds missing prior units, approves a baseline or creates a director agreement.
"""
import json
import os
from pathlib import Path
import pwd
import stat

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_store as storage
from ops.native_maintenance_run_guard import StageRunBinding
from ops.native_maintenance_workflow_pause import encoded, require, unique

MAX_REQUEST = 262144
ROOT = Path('/var/lib/bridge-native-stage-requests')
STAGING_PARENT = Path('/home')
STAGING_NAME = 'bridge-native-stage-submissions'
VERSION = b'NATIVE_STAGE_REQUEST_STORE_V1\n'


def validate_namespace():
    """Loss of either namespace/ledger is recovery, never ordinary submission."""
    storage.trusted_parent(ROOT.parent)
    storage.private_directory(ROOT)
    storage.persistent_mount(ROOT)
    fd = os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        require(set(os.listdir(fd)) == {'VERSION','requests','claims'}, 'REQUEST_NAMESPACE_INCOMPLETE')
        for name in ('requests','claims'): storage.private_directory(ROOT/name)
        marker = storage.open_file(fd,'VERSION',os.O_RDONLY)
        with os.fdopen(marker,'rb') as stream:
            require(stream.read(256) == VERSION, 'REQUEST_NAMESPACE_VERSION')
    finally: os.close(fd)


def first_install_intent(source):
    require(bundle.identifier(source,40), 'REQUEST_PROVISION_SOURCE')
    return encoded(dict(version=1,purpose='first_install_only_not_loss_recovery',source=source,
                        hostname='autopilot-lite-vnic',path=str(ROOT)))


def first_install(source, accepted_digest):
    """Explicit first-use operator action. Never called by submission or fetch."""
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'REQUEST_PROVISION_HOST')
    require(bundle.identifier(accepted_digest,64)
            and bundle.digest(first_install_intent(source)) == accepted_digest, 'REQUEST_PROVISION_NOT_ACCEPTED')
    storage.trusted_parent(ROOT.parent)
    storage.persistent_mount(ROOT.parent)
    ROOT.mkdir(mode=0o700)  # Existing or partial roots refuse; never repair.
    parent = os.open(ROOT.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)
    fd = os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for name in ('requests','claims'):
            os.mkdir(name,0o700,dir_fd=fd)
            os.fsync(fd)
        marker = storage.open_file(fd,'VERSION',os.O_WRONLY|os.O_CREAT|os.O_EXCL)
        with os.fdopen(marker,'wb') as stream:
            stream.write(VERSION); stream.flush(); os.fsync(stream.fileno())
        os.fsync(fd)
        validate_namespace()
    finally: os.close(fd)


class AcceptedRequest:
    def __init__(self, raw, accepted_digest, source):
        require(type(raw) is bytes and 0 < len(raw) <= MAX_REQUEST
                and bundle.identifier(accepted_digest, 64)
                and bundle.digest(raw) == accepted_digest, 'REQUEST_NOT_ACCEPTED')
        value = json.loads(raw, object_pairs_hook=unique)
        require(type(value) is dict and set(value) == {'version', 'request_id', 'source', 'assets', 'packet'}
                and type(value['version']) is int and value['version'] == 1
                and bundle.identifier(value['request_id'], 32)
                and bundle.identifier(source, 40) and value['source'] == source
                and encoded(value) == raw, 'REQUEST_SCHEMA')
        assets, packet = value['assets'], value['packet']
        require(type(assets) is dict and set(assets) == {
            'source_digest', 'manifest_digest', 'baseline_digest', 'envelope_digest'}
            and all(bundle.identifier(v, 64) for v in assets.values()), 'REQUEST_ASSETS')
        require(type(packet) is dict and set(packet) == {
            'version', 'stage', 'scope', 'plan', 'baseline_digest', 'agreement',
            'prior_units', 'accepted_head_digest', 'expected_outcome'}
            and packet['stage'] in ('prepare', 'execute', 'restore')
            and type(packet['scope']) is dict and packet['scope'].get('source') == source
            and packet['scope'].get('manifest_digest') == assets['manifest_digest']
            and packet['baseline_digest'] == assets['baseline_digest'], 'REQUEST_PACKET')
        require(('origin_run' not in packet['scope']) if packet['stage'] == 'prepare'
                else ('origin_run' in packet['scope']), 'REQUEST_ORIGIN')
        self.raw, self.accepted, self.source, self.value = raw, accepted_digest, source, value

    def assert_current(self):
        require(encoded(self.value) == self.raw and bundle.digest(self.raw) == self.accepted,
                'REQUEST_CHANGED')

    def packet_bytes(self, run):
        self.assert_current()
        require(type(run) is StageRunBinding and run.source == self.source, 'REQUEST_RUN_PROFILE')
        run.assert_current()
        packet = json.loads(encoded(self.value['packet']))
        if packet['stage'] == 'prepare':
            packet['scope']['origin_run'] = dict(run_id=run.run_id, attempt=run.attempt, job_id=run.job_id)
        return encoded(packet)


def read_request(accepted_digest):
    """Fixed root-owned private path, no arbitrary filename or path selection."""
    require(bundle.identifier(accepted_digest, 64), 'REQUEST_DIGEST')
    validate_namespace()
    storage.trusted_parent(ROOT.parent)
    root_info = storage.private_directory(ROOT)
    storage.persistent_mount(ROOT)
    root = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    directory = None
    try:
        require((os.fstat(root).st_dev, os.fstat(root).st_ino) ==
                (root_info.st_dev, root_info.st_ino), 'REQUEST_ROOT_CHANGED')
        info = storage.private_directory(ROOT/'requests')
        directory = os.open('requests', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
        require((os.fstat(directory).st_dev, os.fstat(directory).st_ino) ==
                (info.st_dev, info.st_ino), 'REQUEST_DIRECTORY_CHANGED')
        fd = storage.open_file(directory, accepted_digest+'.json', os.O_RDONLY)
        with os.fdopen(fd, 'rb') as stream:
            raw = stream.read(MAX_REQUEST + 1)
        require(0 < len(raw) <= MAX_REQUEST and bundle.digest(raw) == accepted_digest,
                'REQUEST_FILE_DIGEST')
        return raw
    finally:
        if directory is not None: os.close(directory)
        os.close(root)


def claim(request, run):
    """Durable O_EXCL receipt; loss of ACK consumes this request permanently.

    claims must be explicitly provisioned before dispatch; no repair, overwrite,
    retry or cleanup. A fresh approval requires a different accepted request.
    """
    require(type(request) is AcceptedRequest, 'REQUEST_CLAIM_TYPE')
    require(type(run) is StageRunBinding, 'REQUEST_RUN_PROFILE')
    run.assert_running()
    packet = request.packet_bytes(run)
    require(read_request(request.accepted) == request.raw, 'REQUEST_FILE_CHANGED')
    info = storage.private_directory(ROOT/'claims')
    directory = os.open(ROOT/'claims', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require((os.fstat(directory).st_dev, os.fstat(directory).st_ino) ==
                (info.st_dev, info.st_ino), 'REQUEST_CLAIM_DIRECTORY_CHANGED')
        data = encoded(dict(request_digest=request.accepted, packet_digest=bundle.digest(packet),
                            run=dict(run_id=run.run_id, attempt=run.attempt, job_id=run.job_id)))
        fd = storage.open_file(directory, request.accepted+'.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
        fd = storage.open_file(directory, request.accepted+'.json', os.O_RDONLY)
        with os.fdopen(fd, 'rb') as stream:
            require(stream.read(len(data)+1) == data, 'REQUEST_CLAIM_READBACK')
        request.assert_current()
        require(read_request(request.accepted) == request.raw, 'REQUEST_FILE_CHANGED')
        current = storage.private_directory(ROOT/'claims')
        require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino),
                'REQUEST_CLAIM_DIRECTORY_CHANGED')
        run.assert_running()
    finally:
        os.close(directory)


def submit_candidate(raw):
    """Create-only private submission. Returns a digest, never an approval.

    Called by the trusted operator before a separate authenticated dispatch.
    Keep the original bytes off-host; dispatch retention repeats that backup.
    """
    require(type(raw) is bytes and 0 < len(raw) <= MAX_REQUEST, 'REQUEST_SUBMIT_SIZE')
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict and encoded(value) == raw
            and bundle.identifier(value.get('source'),40), 'REQUEST_SUBMIT_CANONICAL')
    validate_namespace()
    root = os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        directory = os.open('requests',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=root)
        try:
            sha = bundle.digest(raw)
            fd = storage.open_file(directory,sha+'.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL)
            with os.fdopen(fd,'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
            require(read_request(sha) == raw, 'REQUEST_SUBMIT_READBACK')
            return sha
        finally: os.close(directory)
    finally: os.close(root)


def staged_candidate(accepted_digest, source):
    """Read data supplied by ubuntu through a fixed dirfd-relative private path.

    This is not a privileged command channel. The exact externally supplied
    digest, canonical schema and pinned source are required before root copying.
    """
    require(bundle.identifier(accepted_digest,64) and bundle.identifier(source,40), 'REQUEST_STAGING_IDENTITY')
    account = pwd.getpwnam('ubuntu')
    require(account.pw_uid > 0 and account.pw_dir == str(STAGING_PARENT/'ubuntu'), 'REQUEST_STAGING_ACCOUNT')
    storage.trusted_parent(STAGING_PARENT)
    ancestor = os.open(STAGING_PARENT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    home = directory = None
    try:
        home = os.open('ubuntu',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=ancestor)
        home_info = os.fstat(home)
        require(home_info.st_uid == account.pw_uid and not home_info.st_mode & 0o022,
                'REQUEST_STAGING_HOME')
        directory = os.open(STAGING_NAME,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=home)
        info = os.fstat(directory)
        require(info.st_uid == account.pw_uid and stat.S_IMODE(info.st_mode) == 0o700,
                'REQUEST_STAGING_DIRECTORY')
        name = accepted_digest+'.json'
        fd = os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
        with os.fdopen(fd,'rb') as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_uid == account.pw_uid
                    and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                    and 0 < before.st_size <= MAX_REQUEST, 'REQUEST_STAGING_FILE')
            raw = stream.read(MAX_REQUEST+1)
            after = os.fstat(stream.fileno())
        def identity(record):
            return (record.st_dev,record.st_ino,record.st_size,record.st_mtime_ns,record.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(after)
                and identity(before) == identity(os.stat(name,dir_fd=directory,follow_symlinks=False)),
                'REQUEST_STAGING_FILE_CHANGED')
        current_home = os.stat('ubuntu',dir_fd=ancestor,follow_symlinks=False)
        current_dir = os.stat(STAGING_NAME,dir_fd=home,follow_symlinks=False)
        require(stat.S_ISDIR(current_home.st_mode) and stat.S_ISDIR(current_dir.st_mode)
                and (current_home.st_dev,current_home.st_ino) == (home_info.st_dev,home_info.st_ino)
                and (current_dir.st_dev,current_dir.st_ino) == (info.st_dev,info.st_ino),
                'REQUEST_STAGING_DIRECTORY_CHANGED')
        validate_submission(raw,accepted_digest,source)
        return raw
    finally:
        for fd in (directory,home,ancestor):
            if fd is not None: os.close(fd)


def validate_submission(raw, accepted_digest, source):
    require(bundle.digest(raw) == accepted_digest, 'REQUEST_STAGING_DIGEST')
    value = json.loads(raw,object_pairs_hook=unique)
    require(type(value) is dict, 'REQUEST_STAGING_SCHEMA')
    if value.get('mode') == 'read_only_rehearsal':
        from ops.native_maintenance_stage_rehearsal import request_value
        request_value(raw,accepted_digest,source)
    elif value.get('mode') == 'stage_inspect':
        from ops.native_maintenance_stage_inspect import input_value
        input_value(raw,accepted_digest,source)
    elif value.get('mode') == 'grant_request_candidate':
        from ops.native_maintenance_grant_candidate import request_value
        request_value(raw,accepted_digest,source)
    else:
        AcceptedRequest(raw,accepted_digest,source)


def resolve_request(accepted_digest, source):
    """Root copy or one exact staged data import; corruption never falls back."""
    try:
        raw = read_request(accepted_digest)
    except FileNotFoundError:
        validate_namespace()  # Only the exact request leaf may be absent.
        raw = staged_candidate(accepted_digest,source)
        require(submit_candidate(raw) == accepted_digest, 'REQUEST_IMPORT_DIGEST')
        require(read_request(accepted_digest) == raw, 'REQUEST_IMPORT_READBACK')
    validate_submission(raw,accepted_digest,source)
    return raw
