"""One create-only private stage-unit receipt over the authenticated duplex pipe.

This is retention, not approval of the packet or SQL. The controller must bind
source/scope/stage/run before construction. A lost ACK cannot be retried by this
instance; reconciliation reads the independently accepted record separately.
"""
import json

from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_checkpoint_transport as rpc
from ops.native_maintenance_runtime import unit_record
from ops.native_maintenance_workflow_pause import encoded, require, unique

PREFIX = 'native-journal/stage-units-v1/'
MAX_BYTES = 4096


def decode(raw, expected):
    require(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES, 'STAGE_UNIT_SIZE')
    value = unit_record(json.loads(raw, object_pairs_hook=unique))
    require(encoded(value) == raw and type(expected) is dict
            and set(expected) == {'source', 'scope_digest', 'stage', 'run'}
            and all(value[key] == item for key, item in expected.items()), 'STAGE_UNIT_BINDING')
    return value


def path(value):
    return PREFIX + value['scope_digest'] + '/' + str(value['run']['run_id']) + '-' + str(value['run']['attempt']) + '.json'


def read_accepted(store, expected, accepted_digest):
    require(isinstance(store, adapter.OCIJournalStore), 'STAGE_UNIT_OCI_REQUIRED')
    try:
        return _read_accepted(store, expected, accepted_digest)
    except BaseException:
        store.failed = True
        raise


def _read_accepted(store, expected, accepted_digest):
    require(isinstance(store, adapter.OCIJournalStore)
            and type(accepted_digest) is str and checkpoint.snapshot._hex(accepted_digest), 'STAGE_UNIT_ACCEPTANCE')
    # Validate the path fields through the same complete record parser before
    # constructing any remote key. Supervisor comes only from the fetched record.
    require(type(expected) is dict and set(expected) == {'source', 'scope_digest', 'stage', 'run'}
            and checkpoint.snapshot._hex(expected['scope_digest'])
            and type(expected['run']) is dict and set(expected['run']) == {'run_id','attempt','job_id'}
            and all(type(v) is int and v > 0 for v in expected['run'].values()), 'STAGE_UNIT_READ_BINDING')
    store.assert_private()
    result = store._read(path(expected), MAX_BYTES)
    require(result is not None and checkpoint.sha(result[0]) == accepted_digest, 'STAGE_UNIT_READBACK')
    decode(result[0], expected)
    store.assert_private()
    return result[0]


class Retainer:
    def __init__(self, store, expected):
        require(isinstance(store, adapter.OCIJournalStore), 'STAGE_UNIT_OCI_REQUIRED')
        self.store = store
        self.expected = json.loads(json.dumps(expected))
        self.used = False

    def retain(self, raw):
        require(not self.used, 'STAGE_UNIT_ALREADY_ATTEMPTED')
        self.used = True
        try:
            value = decode(raw, self.expected)
            self.store.assert_private()
            require(self.store._read(path(value), MAX_BYTES) is None, 'STAGE_UNIT_EXISTS_RECONCILE')
            self.store._budget(len(raw))
            self.store._put(path(value), raw)
            sha = checkpoint.sha(raw)
            require(read_accepted(self.store, self.expected, sha) == raw, 'STAGE_UNIT_READBACK')
            self.store.guard()
            return sha
        except BaseException:
            self.store.failed = True
            raise


class UnitClient:
    def __init__(self, channel, expected):
        require(isinstance(channel, rpc.Channel), 'STAGE_UNIT_CHANNEL_REQUIRED')
        self.channel, self.expected = channel, json.loads(json.dumps(expected))
        self.used = False

    def retain(self, raw):
        require(not self.used, 'STAGE_UNIT_ALREADY_ATTEMPTED')
        self.used = True
        try:
            decode(raw, self.expected)
            self.channel.alive()
            self.channel.send(dict(kind='NATIVE_STAGE_UNIT_RETAIN', binding=self.channel.binding,
                                   data=rpc.pack(raw)))
            reply = self.channel.receive()
            sha = checkpoint.sha(raw)
            require(reply == dict(kind='NATIVE_STAGE_UNIT_ACK', binding=self.channel.binding,
                                  scope_digest=self.expected['scope_digest'], digest=sha), 'STAGE_UNIT_ACK_UNKNOWN')
            self.channel.alive()
            return sha
        except BaseException:
            self.channel.failed = True
            raise


class UnitServer:
    def __init__(self, channel, retainer):
        require(isinstance(channel, rpc.Channel) and type(retainer) is Retainer, 'STAGE_UNIT_SERVER_REQUIRED')
        self.channel, self.retainer = channel, retainer

    def accept(self, request):
        try:
            self.channel.alive()
            require(type(request) is dict and set(request) == {'kind','binding','data'}
                    and request['kind'] == 'NATIVE_STAGE_UNIT_RETAIN'
                    and request['binding'] == self.channel.binding, 'STAGE_UNIT_REQUEST')
            raw = rpc.unpack(request['data'], MAX_BYTES)
            sha = self.retainer.retain(raw)
            self.channel.alive()
            self.channel.send(dict(kind='NATIVE_STAGE_UNIT_ACK', binding=self.channel.binding,
                                   scope_digest=self.retainer.expected['scope_digest'], digest=sha))
        except BaseException:
            self.channel.failed = True
            self.retainer.store.failed = True
            raise
