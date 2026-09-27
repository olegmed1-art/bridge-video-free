"""Bounded synchronous store RPC over an already authenticated private SSH pipe.

No SSH launcher, credentials, operator authority or production default is supplied.
The trusted assembly gives both endpoints the same independently accepted binding
and fixed checkpoint scope. Host owns JournalCheckpoint; runner owns OCIJournalStore.
No frame acknowledgement substitutes for that store's actual completed operation.
"""
import base64
from functools import wraps
import json
import os
import select
import time

from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import encoded, require, unique

MAX_FRAME = 23 * 1024 * 1024
METHODS = {'assert_private', 'read_head', 'read_archive', 'put_archive', 'compare_head'}


def poison(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        try:
            require(not self.failed, 'RPC_PROXY_UNAVAILABLE')
            return method(self, *args, **kwargs)
        except BaseException:
            self.failed = self.channel.failed = True
            raise
    return guarded


def pack(raw):
    require(type(raw) is bytes and 0 < len(raw) <= snapshot.MAX_BYTES, 'RPC_BYTES_SIZE')
    return base64.b64encode(raw).decode('ascii')


def unpack(value, limit):
    require(type(value) is str and 0 < len(value) <= 4 * ((limit + 2) // 3), 'RPC_ENCODED_SIZE')
    raw = base64.b64decode(value, validate=True)
    require(0 < len(raw) <= limit and pack(raw) == value, 'RPC_ENCODING')
    return raw


class Channel:
    """Exclusive owned pipe FDs, one absolute deadline, no buffered read-ahead.

Callers retain descriptor lifetime; no channel operation closes or reconnects it.
This bounds pipe operations, not downstream API calls; the independent supervisor
and the runner's own lifetime remain required. Any protocol/timeout error latches.
"""
    def __init__(self, reader, writer, binding, *, seconds=60):
        require(snapshot._hex(binding) and type(seconds) is int and 1 <= seconds <= 60,
                'RPC_BINDING_OR_LIFETIME')
        require(type(reader) is int and reader >= 0 and type(writer) is int and writer >= 0
                and reader != writer, 'RPC_DESCRIPTORS')
        self.reader, self.writer, self.binding = reader, writer, binding
        self.deadline = time.monotonic() + seconds
        self.failed = False
        os.set_blocking(reader, False)
        os.set_blocking(writer, False)

    def alive(self):
        require(not self.failed and time.monotonic() < self.deadline, 'RPC_UNAVAILABLE')

    def _ready(self, write=False):
        self.alive()
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'RPC_EXPIRED')
        ready = select.select([] if write else [self.reader], [self.writer] if write else [], [], remaining)
        require(bool(ready[1] if write else ready[0]), 'RPC_TIMEOUT')
        self.alive()

    def _read(self, size):
        result = bytearray()
        while len(result) < size:
            self._ready()
            try:
                part = os.read(self.reader, min(size - len(result), 65536))
            except BlockingIOError:
                continue
            require(part, 'RPC_EOF')
            result.extend(part)
        return bytes(result)

    def receive(self):
        try:
            size = int.from_bytes(self._read(4), 'big')
            require(0 < size <= MAX_FRAME, 'RPC_FRAME_SIZE')
            raw = self._read(size)
            value = json.loads(raw, object_pairs_hook=unique)
            require(type(value) is dict and encoded(value) == raw, 'RPC_CANONICAL_FRAME')
            self.alive()
            return value
        except BaseException:
            self.failed = True
            raise

    def send(self, value):
        try:
            self.alive()
            raw = encoded(value)
            require(0 < len(raw) <= MAX_FRAME, 'RPC_FRAME_SIZE')
            pending = memoryview(len(raw).to_bytes(4, 'big') + raw)
            while pending:
                self._ready(write=True)
                try:
                    size = os.write(self.writer, pending[:65536])
                except BlockingIOError:
                    continue
                require(size > 0, 'RPC_WRITE_CLOSED')
                pending = pending[size:]
            self.alive()
        except BaseException:
            self.failed = True
            raise


class ProxyStore:
    def __init__(self, channel, scope):
        require(snapshot._hex(scope), 'RPC_SCOPE')
        self.channel, self.scope = channel, scope
        self.sequence, self.failed = 0, False

    def _call(self, method, args):
        require(not self.failed and method in METHODS, 'RPC_PROXY_UNAVAILABLE')
        try:
            self.sequence += 1
            request = dict(version=1, binding=self.channel.binding, scope=self.scope,
                           sequence=self.sequence, method=method, args=args)
            self.channel.send(request)
            reply = self.channel.receive()
            require(set(reply) == {'version', 'binding', 'scope', 'sequence', 'result'}
                    and type(reply['version']) is int and reply['version'] == 1
                    and reply['binding'] == self.channel.binding and reply['scope'] == self.scope
                    and type(reply['sequence']) is int and reply['sequence'] == self.sequence,
                    'RPC_REPLY_IDENTITY')
            return reply['result']
        except BaseException:
            self.failed = self.channel.failed = True
            raise

    def _scope(self, scope):
        require(scope == self.scope, 'RPC_SCOPE_CHANGED')

    @poison
    def assert_private(self):
        require(self._call('assert_private', []) is None, 'RPC_PRIVATE_RESPONSE')

    @poison
    def read_head(self, scope):
        self._scope(scope)
        value = self._call('read_head', [])
        require(value is None or (type(value) is list and len(value) == 2
                and type(value[1]) is str and 0 < len(value[1]) <= 1024), 'RPC_HEAD_RESPONSE')
        return None if value is None else (unpack(value[0], 4096), value[1])

    @poison
    def read_archive(self, scope, digest, limit):
        self._scope(scope)
        require(snapshot._hex(digest) and type(limit) is int and 0 < limit <= snapshot.MAX_BYTES,
                'RPC_ARCHIVE_REQUEST')
        return unpack(self._call('read_archive', [digest, limit]), limit)

    @poison
    def put_archive(self, scope, digest, data):
        self._scope(scope)
        require(snapshot._hex(digest) and checkpoint.sha(data) == digest
                and snapshot._parse(data)['scope_digest'] == scope, 'RPC_ARCHIVE_BINDING')
        require(self._call('put_archive', [digest, pack(data)]) is None, 'RPC_PUT_RESPONSE')

    @poison
    def compare_head(self, scope, revision, data):
        self._scope(scope)
        checkpoint.parse_head(data, scope)
        require(revision is None or (type(revision) is str and 0 < len(revision) <= 1024),
                'RPC_REVISION')
        require(self._call('compare_head', [revision, pack(data)]) is None, 'RPC_CAS_RESPONSE')


class StoreServer:
    """One synchronous request at a time; guards enclose each real operation.

guard verifies the actual authenticated source/run/lifetime and accepted scope.
The supplied store also keeps its own pre-mutation guard. No error text or error
frame is sent: on any ambiguity the channel and server are permanently poisoned.
"""
    def __init__(self, channel, scope, store, guard):
        require(snapshot._hex(scope) and callable(guard), 'RPC_SERVER_REQUIRED')
        self.channel, self.scope, self.store, self.guard = channel, scope, store, guard
        self.sequence, self.failed = 0, False

    def once(self):
        require(not self.failed, 'RPC_SERVER_UNAVAILABLE')
        try:
            request = self.channel.receive()
            self.accept(request)
        except BaseException:
            self.failed = self.channel.failed = True
            raise

    def accept(self, request):
        """Dispatch one already framed request from the exclusive trusted loop."""
        require(not self.failed, 'RPC_SERVER_UNAVAILABLE')
        try:
            require(set(request) == {'version', 'binding', 'scope', 'sequence', 'method', 'args'}
                    and type(request['version']) is int and request['version'] == 1
                    and request['binding'] == self.channel.binding and request['scope'] == self.scope
                    and type(request['sequence']) is int and request['sequence'] == self.sequence + 1
                    and type(request['method']) is str and request['method'] in METHODS
                    and type(request['args']) is list, 'RPC_REQUEST_IDENTITY')
            self.sequence = request['sequence']
            self.guard()
            self.channel.alive()
            result = self._dispatch(request['method'], request['args'])
            self.guard()
            self.channel.alive()
            self.channel.send(dict(version=1, binding=self.channel.binding, scope=self.scope,
                                   sequence=self.sequence, result=result))
        except BaseException:
            self.failed = self.channel.failed = True
            raise

    def _dispatch(self, method, args):
        if method in ('assert_private', 'read_head'):
            require(not args, 'RPC_ARGUMENTS')
            if method == 'assert_private':
                return self.store.assert_private()
            head = self.store.read_head(self.scope)
            if head is None:
                return None
            checkpoint.parse_head(head[0], self.scope)
            require(type(head[1]) is str and 0 < len(head[1]) <= 1024, 'RPC_HEAD_REVISION')
            return [pack(head[0]), head[1]]
        require(len(args) == 2, 'RPC_ARGUMENTS')
        first, second = args
        if method == 'read_archive':
            require(snapshot._hex(first) and type(second) is int and 0 < second <= snapshot.MAX_BYTES,
                    'RPC_ARCHIVE_REQUEST')
            raw = self.store.read_archive(self.scope, first, second)
            require(checkpoint.sha(raw) == first and len(raw) <= second, 'RPC_ARCHIVE_RESPONSE')
            return pack(raw)
        if method == 'put_archive':
            raw = unpack(second, snapshot.MAX_BYTES)
            require(snapshot._hex(first) and checkpoint.sha(raw) == first
                    and snapshot._parse(raw)['scope_digest'] == self.scope, 'RPC_ARCHIVE_BINDING')
            return self.store.put_archive(self.scope, first, raw)
        require(method == 'compare_head' and (first is None or
                (type(first) is str and 0 < len(first) <= 1024)), 'RPC_REVISION')
        raw = unpack(second, 4096)
        checkpoint.parse_head(raw, self.scope)
        return self.store.compare_head(self.scope, first, raw)
