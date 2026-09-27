"""Fixed production host command. No diagnostic caller may select this command."""
import os

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_checkpoint_transport as rpc
from ops.native_maintenance_owner_host import loaded_runtime
from ops.native_maintenance_stage_request import AcceptedRequest, read_request
from ops.native_maintenance_run_guard import API, StageRunBinding
from ops.native_maintenance_workflow_pause import require


def main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope):
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'STAGE_HOST')
    require(type(envelope) is dict and set(envelope) == {
        'driver', 'credential', 'token', 'request', 'manifest', 'job_id'}, 'STAGE_ENVELOPE')
    raw = rpc.unpack(envelope['request'], 262144)
    require(read_request(request_digest) == raw, 'STAGE_REQUEST_CHANGED')
    request = AcceptedRequest(raw, request_digest, source)
    wheels = rpc.unpack(envelope['driver'], 10*1024*1024)
    require(bundle.digest(wheels) == wheel_digest, 'STAGE_DRIVER_DIGEST')
    channel = rpc.Channel(0, 1, binding)
    with loaded_runtime(wheels) as (psycopg, _):
        # Import permission machinery only after source and driver verification.
        from ops import native_maintenance_runtime as runtime
        from ops.native_maintenance_stage_unit import UnitClient
        from ops.native_maintenance_owner_attest import parameters
        manifest = rpc.unpack(envelope['manifest'], 4*1024*1024)
        run = StageRunBinding(source, run_id, attempt, API(envelope['token']))
        run.assert_running()
        require(type(envelope['job_id']) is int and run.job_id == envelope['job_id'], 'STAGE_JOB_CHANGED')
        packet = runtime.DerivedStagePacket(request, run, manifest)
        kwargs = parameters(envelope['credential'])
        def connect():
            return psycopg.connect(**kwargs, autocommit=True)
        expected = dict(source=source, scope_digest=packet.scope_digest, stage=packet.stage,
                        run=runtime.run_identity(run))
        store = rpc.ProxyStore(channel, packet.scope_digest)
        result = runtime.stage(packet, run=run, store=store, connect=connect,
                               api_token=envelope['token'], retain_unit=UnitClient(channel, expected).retain)
        packet.assert_bound(run)
        channel.alive()
        channel.send(dict(kind='NATIVE_STAGE_COMPLETE', binding=binding,
                          request_digest=request_digest, result=result, sequence=store.sequence))
