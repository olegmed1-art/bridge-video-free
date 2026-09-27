"""Separate read-only host command, with synthetic journal writes only.

This module never imports runtime/executor or calls workflow pause/restore. Its
owner connections are READ ONLY before their first statement. Its observations
and elapsed time are estimates, not writer exclusion or permission acceptance.
"""
import json
import os
import time

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_checkpoint_transport as rpc
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_checkpoint_host_probe import identity, journals
from ops.native_maintenance_owner_host import loaded_runtime
from ops.native_maintenance_stage_request import read_request
from ops.native_maintenance_run_guard import API, RehearsalRunBinding
from ops.native_maintenance_supervisor import SelfSupervisor
from ops.native_maintenance_workflow_pause import digest, encoded, require, unique, validate_plan


def request_value(raw, accepted_digest, source):
    require(type(raw) is bytes and 0 < len(raw) <= 262144
            and bundle.digest(raw) == accepted_digest, 'REHEARSAL_REQUEST_DIGEST')
    value = json.loads(raw, object_pairs_hook=unique)
    require(type(value) is dict and set(value) == {'version', 'mode', 'source', 'plan'}
            and type(value['version']) is int and value['version'] == 1
            and value['mode'] == 'read_only_rehearsal' and value['source'] == source
            and encoded(value) == raw, 'REHEARSAL_REQUEST_SCHEMA')
    validate_plan(value['plan'])
    require(value['plan']['source'] == source, 'REHEARSAL_PLAN_SOURCE')
    return value


def main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope):
    require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'REHEARSAL_HOST')
    require(type(envelope) is dict and set(envelope) == {
        'driver', 'credential', 'token', 'request', 'job_id'}, 'REHEARSAL_ENVELOPE')
    raw = rpc.unpack(envelope['request'], 262144)
    require(read_request(request_digest) == raw, 'REHEARSAL_REQUEST_CHANGED')
    request = request_value(raw, request_digest, source)
    wheels = rpc.unpack(envelope['driver'], 10*1024*1024)
    require(bundle.digest(wheels) == wheel_digest, 'REHEARSAL_DRIVER_DIGEST')
    started = time.monotonic()
    channel = rpc.Channel(0, 1, binding)
    run = RehearsalRunBinding(source, run_id, attempt, API(envelope['token']))
    run.assert_running()
    require(type(envelope['job_id']) is int and run.job_id == envelope['job_id'], 'REHEARSAL_JOB_CHANGED')
    supervisor = SelfSupervisor(source, run)
    supervisor.assert_exclusive()
    from ops import oracle_light_active_hold_attest as hold
    before = hold.attest()
    with loaded_runtime(wheels) as (psycopg, _):
        from ops import native_maintenance_owner_attest as owner
        from ops.native_maintenance_coordination import OwnedConnections, WorkflowDrain
        from ops.native_maintenance_workflow_api import WorkflowAPI
        from database import native_cli_permission_engine as engine
        from ops.native_permission_hold_guard import EXPECTED_TARGET
        target = engine.Target(**{**EXPECTED_TARGET, 'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
        kwargs = owner.parameters(envelope['credential'])
        def connect():
            conn = psycopg.connect(**kwargs, autocommit=True)
            try:
                conn.read_only = True
                return conn
            except BaseException:
                conn.close()
                raise
        # Real read-only identity/snapshot and owned-backend drain. No Agreement.
        report = owner.observe(psycopg.connect, envelope['credential'])
        connections = OwnedConnections(connect, target, approved_hold=before)
        plan = request['plan']
        workflows = WorkflowAPI(envelope['token'], plan, digest(plan))
        for row in plan['workflows']:
            require(workflows.get_workflow(row['id']) == row, 'REHEARSAL_WORKFLOW_CHANGED')
        WorkflowDrain(run.api, plan, digest(plan)).assert_drained()
        connections.assert_drained()
        require(hold.attest() == before, 'REHEARSAL_HOLD_CHANGED')
        run.assert_running()
        synthetic_plan, scope, scope_digest, _ = identity(source, run_id, attempt)
        store = rpc.ProxyStore(channel, scope_digest)
        with journals(scope_digest) as (operation, pause, parent):
            operation.append(dict(kind='BOUND', scope=scope))
            pause.append(dict(kind='PLAN', plan=synthetic_plan, digest=digest(synthetic_plan),
                              operation_scope_digest=scope_digest))
            protocol = checkpoint.JournalCheckpoint(store)
            protocol.sync(scope_digest, operation, pause)
            operation.append(dict(kind='SYNTHETIC_INTENT', outcome='UNKNOWN'))
            head = protocol.sync(scope_digest, operation, pause)
            data = checkpoint.accepted_latest(store, scope_digest, head)
            restored = snapshot.restore(data, checkpoint.sha(data), scope_digest, parent)
            require(snapshot.capture(restored/'operation', restored/'pause') == data,
                    'REHEARSAL_RESTORE')
        connections.assert_drained()
        require(hold.attest() == before, 'REHEARSAL_FINAL_HOLD')
        supervisor.assert_alive()
        run.assert_running()
        channel.send(dict(kind='NATIVE_REHEARSAL_COMPLETE', binding=binding,
            request_digest=request_digest, scope_digest=scope_digest, head_digest=head,
            sequence=store.sequence, elapsed_ms=int((time.monotonic()-started)*1000),
            snapshot_digest=report['snapshot_digest'], timing_is_estimate=True,
            production_mutations=False, snapshot_approved=False,supervisor=supervisor.record))
