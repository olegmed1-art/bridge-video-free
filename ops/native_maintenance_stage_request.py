"""Launcher request provenance and durable single-use admission.

File presence is not approval. accepted_digest is supplied by the authenticated
owner/manual workflow, outside these bytes. No helper selects an observed head,
adds missing prior units, approves a baseline or creates a director agreement.
"""
import json
import os
from pathlib import Path

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_store as storage
from ops.native_maintenance_run_guard import StageRunBinding
from ops.native_maintenance_workflow_pause import encoded, require, unique

MAX_REQUEST = 262144
ROOT = Path('/var/lib/bridge-native-stage-requests')


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
    storage.trusted_parent(ROOT.parent)
    storage.persistent_mount(ROOT.parent)
    try: ROOT.mkdir(mode=0o700)
    except FileExistsError: pass
    storage.private_directory(ROOT)
    parent = os.open(ROOT.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(parent)
    finally: os.close(parent)
    root = os.open(ROOT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        require(set(os.listdir(root)) <= {'requests','claims'}, 'REQUEST_STORE_RECONCILIATION')
        for name in ('requests','claims'):
            try: os.mkdir(name,0o700,dir_fd=root)
            except FileExistsError: pass
            storage.private_directory(ROOT/name)
            os.fsync(root)
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
