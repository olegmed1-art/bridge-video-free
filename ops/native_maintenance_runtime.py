"""Host-side staged assembly. No CLI, credential loading or automatic approvals.

The fixed launcher must verify the source bundle/driver, supply independently
accepted packet bytes and retain_unit: a synchronous private off-VM create-only
write + exact readback. Its digest ACK is mandatory before executor construction.
No callback/default here fabricates that acceptance. The launcher remains a
separate reviewed dependency; this module must not be called from diagnostic code.
"""
from contextlib import contextmanager
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import re

from database import native_cli_permission_engine as engine
from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_coordination as coordination
from ops import native_maintenance_lifetime as lifetime
from ops import native_maintenance_recovery_assets as assets
from ops import native_maintenance_store as storage
from ops import oracle_light_active_hold_attest as hold
from ops.native_maintenance_executor import MaintenanceExecutor, operation_scope
from ops.native_maintenance_run_guard import StageRunBinding
from ops.native_maintenance_supervisor import SelfSupervisor
from ops.native_maintenance_workflow_api import Transport
from ops.native_maintenance_workflow_pause import Journal, digest, encoded, require, unique, validate_plan

STAGES = ('prepare', 'execute', 'restore')


def run_identity(run):
    return dict(run_id=run.run_id, attempt=run.attempt, job_id=run.job_id)


def unit_record(value):
    require(type(value) is dict and set(value) == {
        'version', 'kind', 'stage', 'source', 'scope_digest', 'run', 'supervisor'}
        and type(value['version']) is int and value['version'] == 1
        and value['kind'] == 'NATIVE_STAGE_UNIT' and value['stage'] in STAGES,
        'RUNTIME_UNIT_SCHEMA')
    require(re.fullmatch('[0-9a-f]{40}', value['source'])
            and re.fullmatch('[0-9a-f]{64}', value['scope_digest']), 'RUNTIME_UNIT_BINDING')
    run, supervisor = value['run'], value['supervisor']
    require(type(run) is dict and set(run) == {'run_id', 'attempt', 'job_id'}
            and all(type(v) is int and v > 0 for v in run.values()), 'RUNTIME_UNIT_RUN')
    require(type(supervisor) is dict and set(supervisor) == {'unit', 'invocation', 'cgroup_inode'}
            and re.fullmatch(lifetime.UNIT, supervisor['unit'])
            and supervisor['unit'].startswith('bridge-native-ro-' + value['source'][:12] + '-'
                                             + str(run['run_id']) + '-' + str(run['attempt']) + '-')
            and re.fullmatch('[0-9a-f]{32}', supervisor['invocation'])
            and type(supervisor['cgroup_inode']) is int and supervisor['cgroup_inode'] > 0,
            'RUNTIME_UNIT_SUPERVISOR')
    return value


class Packet:
    """Structural/integrity validation only; subclasses supply acceptance provenance."""
    def __init__(self, raw, accepted_digest, manifest):
        require(type(raw) is bytes and 0 < len(raw) <= 262144
                and type(accepted_digest) is str
                and re.fullmatch('[0-9a-f]{64}', accepted_digest)
                and checkpoint.sha(raw) == accepted_digest, 'RUNTIME_PACKET_NOT_ACCEPTED')
        value = json.loads(raw, object_pairs_hook=unique)
        require(type(value) is dict and set(value) == {
            'version', 'stage', 'scope', 'plan', 'baseline_digest', 'agreement',
            'prior_units', 'accepted_head_digest', 'expected_outcome'}
            and type(value['version']) is int and value['version'] == 1
            and value['stage'] in STAGES and encoded(value) == raw, 'RUNTIME_PACKET_SCHEMA')
        self.value, self.raw, self.accepted = value, raw, accepted_digest
        self.stage, self.scope, self.plan = value['stage'], value['scope'], value['plan']
        validate_plan(self.plan)
        require(type(self.scope) is dict and self.scope.get('target') == assets.EXPECTED_TARGET,
                'RUNTIME_TARGET')
        self.target = engine.Target(**{**assets.EXPECTED_TARGET,
                                      'neon': engine.NeonBinding(**assets.EXPECTED_TARGET['neon'])})
        self.hold = hold.HoldIdentity(**self.scope['hold'])
        expected = operation_scope(target=self.target, operation=self.scope['operation'],
            manifest_digest=self.scope['manifest_digest'], plan_digest=digest(self.plan),
            source=self.plan['source'], expected_route=self.scope['route'], approved_hold=self.hold,
            origin_run=self.scope['origin_run'], staged=True, observed_admission=True)
        require(self.scope == expected and self.scope['route'] ==
                dict(version=1, backend='neon', database='autopilot', epoch=0), 'RUNTIME_SCOPE')
        self.scope_digest = digest(self.scope)
        assets.manifest(manifest, self.scope['manifest_digest'], value['baseline_digest'])
        self.manifest = manifest
        head = value['accepted_head_digest']
        require((self.stage == 'prepare' and head is None and value['prior_units'] == [])
                or (self.stage != 'prepare' and type(head) is str and re.fullmatch('[0-9a-f]{64}', head)),
                'RUNTIME_HEAD_ACCEPTANCE')
        require(value['expected_outcome'] in ('BEFORE', 'AFTER') if self.stage == 'restore'
                else value['expected_outcome'] is None, 'RUNTIME_OUTCOME')
        require(type(value['prior_units']) is list and len(value['prior_units']) <= 16,
                'RUNTIME_PRIOR_UNITS')
        self.prior = [unit_record(row) for row in value['prior_units']]
        require(all(row['scope_digest'] == self.scope_digest and row['source'] == self.scope['source']
                    for row in self.prior)
                and len({(r['run']['run_id'], r['run']['attempt']) for r in self.prior}) == len(self.prior)
                and len({r['supervisor']['unit'] for r in self.prior}) == len(self.prior),
                'RUNTIME_PRIOR_BINDING')
        if self.stage != 'prepare':
            require(any(r['stage'] == 'prepare' and r['run'] == self.scope['origin_run']
                        for r in self.prior), 'RUNTIME_ORIGIN_UNIT_REQUIRED')
        # The separately accepted packet includes these exact agreement bytes;
        # this hash does not promote a candidate or originate a new agreement.
        self.agreement = coordination.Agreement(value['agreement'], digest(value['agreement']), self.scope)

    def assert_current(self):
        require(checkpoint.sha(self.raw) == self.accepted and encoded(self.value) == self.raw
                and checkpoint.sha(self.manifest) == self.scope['manifest_digest'],
                'RUNTIME_PACKET_CHANGED')
        self.agreement.assert_held(self.scope_digest)


class AcceptedPacket(Packet):
    """Exact packet bytes accepted outside this host run; not a derived approval."""


class DerivedStagePacket(Packet):
    """Deterministic projection of an externally accepted request and observed run.

    The generated packet digest binds transport bytes only. Every scope field
    except prepare's origin identity comes from AcceptedRequest; the host repeats
    that derivation with its own authenticated run before any stage operation.
    """
    def __init__(self, request, run, manifest):
        from ops.native_maintenance_stage_request import AcceptedRequest
        require(type(request) is AcceptedRequest and type(run) is StageRunBinding,
                'RUNTIME_DERIVATION_COMPONENTS')
        raw = request.packet_bytes(run)
        super().__init__(raw, checkpoint.sha(raw), manifest)
        self.request = request

    def assert_bound(self, run):
        require(self.request.packet_bytes(run) == self.raw, 'RUNTIME_DERIVATION_CHANGED')
        self.assert_current()



def read_private(directory, name, limit):
    fd = storage.open_file(directory, name, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(limit + 1)
    require(0 < len(data) <= limit, 'RUNTIME_PRIVATE_FILE_SIZE')
    return data


def create_private(directory, name, data):
    fd = storage.open_file(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.fsync(directory)
    require(read_private(directory, name, len(data)) == data, 'RUNTIME_PRIVATE_READBACK')


@contextmanager
def locked_scope(packet):
    """Existing provisioned persistent store only. Failed partial scopes stay intact."""
    require(os.getuid() == 0, 'RUNTIME_ROOT')
    storage.trusted_parent(storage.PARENT)
    root = storage.PARENT / storage.NAME
    info = storage.private_directory(root)
    storage.persistent_mount(root)
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    lock = directory = units = None
    try:
        require((os.fstat(parent).st_dev, os.fstat(parent).st_ino) == (info.st_dev, info.st_ino),
                'RUNTIME_STORE_CHANGED')
        lock = storage.open_file(parent, 'lock', os.O_RDWR)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require(read_private(parent, 'VERSION', 256) == storage.VERSION, 'RUNTIME_STORE_VERSION')
        fresh = packet.stage == 'prepare'
        if fresh:
            os.mkdir(packet.scope_digest, 0o700, dir_fd=parent)
            os.fsync(parent)
        base = root / packet.scope_digest
        storage.private_directory(base)
        directory = os.open(packet.scope_digest, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        if fresh:
            for name in ('operation', 'pause', 'units'):
                os.mkdir(name, 0o700, dir_fd=directory)
                os.fsync(directory)
            create_private(directory, 'manifest.json', packet.manifest)
        require(set(os.listdir(directory)) == {'operation', 'pause', 'units', 'manifest.json'}
                and read_private(directory, 'manifest.json', assets.MAX_MANIFEST) == packet.manifest,
                'RUNTIME_SCOPE_FILES')
        for name in ('operation', 'pause', 'units'):
            storage.private_directory(base / name)
        units = os.open('units', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        existing = {}
        for name in os.listdir(units):
            require(re.fullmatch(r'[1-9][0-9]{0,19}-[1-9][0-9]{0,5}\.json', name), 'RUNTIME_UNIT_FILE')
            raw = read_private(units, name, 4096)
            row = unit_record(json.loads(raw, object_pairs_hook=unique))
            require(encoded(row) == raw and name == str(row['run']['run_id']) + '-'
                    + str(row['run']['attempt']) + '.json', 'RUNTIME_UNIT_FILE_BINDING')
            existing[name] = row
        accepted = {str(r['run']['run_id']) + '-' + str(r['run']['attempt']) + '.json': r for r in packet.prior}
        # Exact equality prevents omitting an uncertain local stage or filling in
        # a missing disk record from an unverified claim. Lost disks require the
        # separately reviewed offline recovery path, not this launcher.
        require(existing == accepted, 'RUNTIME_PRIOR_SET_RECONCILIATION_REQUIRED')
        with Journal(base / 'operation') as operation, Journal(base / 'pause') as pause:
            yield base / 'manifest.json', operation, pause, units
        require((base.stat().st_dev, base.stat().st_ino) ==
                (os.fstat(directory).st_dev, os.fstat(directory).st_ino)
                and ((base/'units').stat().st_dev, (base/'units').stat().st_ino) ==
                (os.fstat(units).st_dev, os.fstat(units).st_ino), 'RUNTIME_SCOPE_DIRECTORY_CHANGED')
        require((root.stat().st_dev, root.stat().st_ino) == (info.st_dev, info.st_ino),
                'RUNTIME_STORE_CHANGED')
    finally:
        for fd in (units, directory, lock, parent):
            if fd is not None:
                os.close(fd)


def stage(packet, *, run, store, connect, api_token, retain_unit):
    """One host stage. No retries, automatic restore, activation or pilot.

    connect must be the fixed owner factory from the verified driver/credential
    launcher. retain_unit sends canonical bytes to the authenticated runner and
    returns their SHA only after private off-VM write+readback. No default exists.
    """
    require(type(packet) is DerivedStagePacket and type(run) is StageRunBinding and run.launcher is False
            and callable(connect) and callable(retain_unit), 'RUNTIME_COMPONENTS')
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'RUNTIME_HOST')
    packet.assert_current()
    run.assert_running()
    packet.assert_bound(run)
    current = run_identity(run)
    require(run.source == packet.scope['source'] and all(row['run']['run_id'] != run.run_id for row in packet.prior),
            'RUNTIME_RUN_REUSED')
    require((packet.stage == 'prepare' and current == packet.scope['origin_run'])
            or (packet.stage != 'prepare' and run.run_id != packet.scope['origin_run']['run_id']),
            'RUNTIME_STAGE_RUN')
    supervisor = SelfSupervisor(run.source, run)
    supervisor.assert_exclusive()
    from ops.native_maintenance_stage_request import claim
    claim(packet.request, run)
    own = unit_record(dict(version=1, kind='NATIVE_STAGE_UNIT', stage=packet.stage, source=run.source,
                           scope_digest=packet.scope_digest, run=current, supervisor=supervisor.record))
    connections = coordination.OwnedConnections(connect, packet.target, approved_hold=packet.hold)
    prior = [row['supervisor'] for row in packet.prior]
    operator = coordination.Operator(scope=packet.scope, agreement=packet.agreement,
        workflows=coordination.WorkflowDrain(run.api, packet.plan, digest(packet.plan)),
        prior_hosts=coordination.PriorSupervisors(prior, digest(prior)), connections=connections,
        source_transport=Transport(api_token), run=run)
    with locked_scope(packet) as (manifest, operation, pause, units):
        if packet.stage != 'prepare':
            require(operation.records and operation.records[0]['event'] == dict(kind='BOUND', scope=packet.scope),
                    'RUNTIME_BOUND_JOURNAL')
            recorded_runs = [packet.scope['origin_run']] + [r['event']['run'] for r in operation.records[1:]]
            require(all(any(prior['run'] == identity for prior in packet.prior) for identity in recorded_runs),
                    'RUNTIME_RECORDED_HOST_OMITTED')
        operator.assert_drained(packet.scope_digest)
        require(hold.attest() == packet.hold, 'RUNTIME_HOLD_CHANGED')
        raw = encoded(own)
        create_private(units, str(run.run_id) + '-' + str(run.attempt) + '.json', raw)
        # Durable local record survives lost ACK. Do not retry this callback.
        require(retain_unit(raw) == checkpoint.sha(raw), 'RUNTIME_UNIT_ACK_UNKNOWN')
        packet.assert_current()
        supervisor.assert_alive()
        run.assert_running()
        barrier = checkpoint.JournalCheckpoint(store, accepted_head_digest=packet.value['accepted_head_digest'])
        if packet.stage == 'restore':
            barrier.accept_resume(packet.scope_digest, operation, pause)
        ex = MaintenanceExecutor(target=packet.target, operation=packet.scope['operation'],
            manifest_path=manifest, manifest_digest=packet.scope['manifest_digest'],
            expected_route=packet.scope['route'], approved_hold=packet.hold, workflow_plan=packet.plan,
            plan_digest=digest(packet.plan), api_token=api_token, pause_journal=pause,
            operation_journal=operation, run=run, operator=operator, lifetime=supervisor, checkpoint=barrier,
            staged=True, observed_admission=True)
        require(ex.scope == packet.scope, 'RUNTIME_EXECUTOR_SCOPE')
        if packet.stage == 'prepare':
            head = ex.prepare()
            outcome = 'UNKNOWN'
        elif packet.stage == 'execute':
            outcome = ex.execute_prepared(connections.open)
            head = checkpoint.sha(barrier.head[0])
        else:
            outcome = packet.value['expected_outcome']
            class Release:
                def assert_reconciled(self, scope, expected):
                    require(scope == packet.scope_digest and expected == outcome, 'RUNTIME_RELEASE_SCOPE')
                    packet.assert_current()
                    supervisor.assert_alive()
                    operator.assert_drained(scope)
                    require(hold.attest() == packet.hold, 'RUNTIME_RELEASE_HOLD')
                    with connections.open() as observer:
                        require(engine.inspect(observer, packet.target, manifest, packet.scope['manifest_digest']) == expected,
                                'RUNTIME_RELEASE_DATABASE_DRIFT')
                    run.assert_running()
                    supervisor.assert_alive()
                    packet.assert_current()
            ex.restore(Release(), outcome)
            head = checkpoint.sha(barrier.head[0])
        packet.assert_current()
        supervisor.assert_alive()
        run.assert_running()
        require(hold.attest() == packet.hold, 'RUNTIME_FINAL_HOLD')
        return dict(stage=packet.stage, scope_digest=packet.scope_digest, head_digest=head,
                    outcome=outcome, unit_digest=checkpoint.sha(raw), host_exited=False)
