"""Fixed production host command. No diagnostic caller may select this command."""
import os

from ops.native_maintenance_budgets import admit_host
from ops.native_maintenance_supervisor import StageSupervisor

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_checkpoint_transport as rpc
from ops.native_maintenance_owner_host import loaded_runtime
from ops.native_maintenance_stage_request import AcceptedRequest, read_request
from ops.native_maintenance_run_guard import PersistentAPI as API, StageRunBinding
from ops.native_maintenance_workflow_pause import require
from ops.native_maintenance_stage_rehearsal import SAFE_CODES as READ_ONLY_CODES, failure_code as read_only_failure_code


PHASES = frozenset(('request', 'driver', 'run', 'packet', 'admission', 'runtime', 'completion'))
SAFE_CODES = READ_ONLY_CODES | frozenset((
    'RUNTIME_COMPONENTS', 'RUNTIME_HOST', 'RUNTIME_RUN_REUSED', 'RUNTIME_STAGE_RUN',
    'RUNTIME_STORE_VERSION', 'RUNTIME_STORE_CHANGED', 'RUNTIME_SCOPE_FILES',
    'RUNTIME_UNIT_FILE', 'RUNTIME_UNIT_FILE_BINDING', 'RUNTIME_PRIVATE_FILE_SIZE',
    'RUNTIME_PRIOR_SET_RECONCILIATION_REQUIRED', 'RUNTIME_BOUND_JOURNAL',
    'RUNTIME_RECORDED_HOST_OMITTED', 'RUNTIME_HOLD_CHANGED', 'RUNTIME_UNIT_ACK_UNKNOWN',
    'RUNTIME_RELEASE_DATABASE_DRIFT', 'COORDINATION_ORIGIN_HOST_EVIDENCE_REQUIRED',
    'COORDINATION_AGREEMENT_EXPIRED', 'COORDINATION_AGREEMENT_NOT_CURRENT',
    'DRAIN_PRIOR_HOST_ACTIVE', 'DRAIN_PRIOR_CGROUP_ACTIVE', 'DRAIN_PRIOR_CGROUP_PRESENT',
    'REQUEST_ALREADY_CLAIMED', 'RUN_BINDING_EXPIRED', 'RUN_NOT_RUNNING', 'JOB_NOT_RUNNING'))


def failure_code(exc):
    code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
    return code if code in SAFE_CODES else read_only_failure_code(exc)


def main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope):
    diagnostic = dict(phase='request', channel=None)
    try:
        with API(envelope['token']) as api:
            return _main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope, api, diagnostic)
    except BaseException as exc:
        channel = diagnostic['channel']
        if channel is not None:
            try:
                channel.send(dict(kind='NATIVE_STAGE_REFUSED', binding=binding,
                    request_digest=request_digest, phase=diagnostic['phase'], code=failure_code(exc)))
            except BaseException:
                pass  # Preserve the original failure; never reopen or retry the pipe.
        raise


def _main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope, api, diagnostic):
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'STAGE_HOST')
    require(type(envelope) is dict and set(envelope) == {
        'driver', 'credential', 'token', 'request', 'manifest', 'job_id'}, 'STAGE_ENVELOPE')
    raw = rpc.unpack(envelope['request'], 262144)
    require(read_request(request_digest) == raw, 'STAGE_REQUEST_CHANGED')
    request = AcceptedRequest(raw, request_digest, source)
    wheels = rpc.unpack(envelope['driver'], 10*1024*1024)
    require(bundle.digest(wheels) == wheel_digest, 'STAGE_DRIVER_DIGEST')
    channel = rpc.Channel(0, 1, binding, seconds=rpc.STAGE_RPC_SECONDS)
    diagnostic.update(channel=channel, phase='driver')
    with loaded_runtime(wheels) as (psycopg, _):
        # Import permission machinery only after source and driver verification.
        from ops import native_maintenance_runtime as runtime
        from ops.native_maintenance_stage_unit import UnitClient
        from ops.native_maintenance_owner_attest import parameters
        manifest = rpc.unpack(envelope['manifest'], 4*1024*1024)
        diagnostic['phase'] = 'run'
        run = StageRunBinding(source, run_id, attempt, api)
        run.assert_running()
        require(type(envelope['job_id']) is int and run.job_id == envelope['job_id'], 'STAGE_JOB_CHANGED')
        diagnostic['phase'] = 'packet'
        packet = runtime.DerivedStagePacket(request, run, manifest)
        kwargs = parameters(envelope['credential'])
        def connect():
            return psycopg.connect(**kwargs, autocommit=True)
        expected = dict(source=source, scope_digest=packet.scope_digest, stage=packet.stage,
                        run=runtime.run_identity(run))
        supervisor = StageSupervisor(source, run)
        def guard():
            run.assert_running()
            packet.assert_bound(run)
        diagnostic['phase'] = 'admission'
        admit_host(channel, request_digest, supervisor, guard)
        store = rpc.ProxyStore(channel, packet.scope_digest)
        diagnostic['phase'] = 'runtime'
        result = runtime.stage(packet, run=run, store=store, connect=connect,
                               api_token=envelope['token'], retain_unit=UnitClient(channel, expected).retain)
        diagnostic['phase'] = 'completion'
        packet.assert_bound(run)
        channel.alive()
        channel.send(dict(kind='NATIVE_STAGE_COMPLETE', binding=binding,
                          request_digest=request_digest, result=result, sequence=store.sequence))
