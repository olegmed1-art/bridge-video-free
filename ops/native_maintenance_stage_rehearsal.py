"""Separate read-only host command, with synthetic journal writes only.

This module never imports runtime/executor or calls workflow pause/restore. Its
owner connections are READ ONLY before their first statement. Its observations
and elapsed time are estimates, not writer exclusion or permission acceptance.
"""
import json
import os
import sys
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


PHASES = frozenset(('request', 'run_authentication', 'supervisor', 'hold', 'driver',
    'owner_snapshot', 'workflow_state', 'workflow_drain', 'backend_drain', 'hold_recheck',
    'checkpoint', 'restore', 'final_drain', 'completion'))
SAFE_CODES = frozenset(('REFUSED', 'OWNER_DRIVER_ALREADY_IMPORTED', 'OWNER_DRIVER_ORIGIN',
    'OWNER_DRIVER_VERSION', 'OWNER_DRIVER_SUBMODULE_ORIGIN', 'WORKFLOW_NOT_DRAINED',
    'DATABASE_NOT_DRAINED', 'DATABASE_ACTIVITY_INCOMPLETE', 'DATABASE_PREPARED_TRANSACTION_PRESENT',
    'REHEARSAL_WORKFLOW_CHANGED', 'RUN_BINDING_EXPIRED', 'API_RESPONSE_INCOMPLETE',
    'RUNTIME_OTHER_SUPERVISOR_ACTIVE', 'RUNTIME_OTHER_CGROUP_ACTIVE', 'RPC_TIMEOUT', 'RPC_EOF',
    'DRAIN_HOLD_IDENTITY_REQUIRED', 'DRAIN_OWNED_CONNECTION_REQUIRED', 'DRAIN_BACKEND_IDENTITY',
    'DRAIN_OWNED_BACKEND_LOST', 'DRAIN_OWNED_BACKEND_MISSING', 'DRAIN_HOLD_CHANGED',
    'CONFIG_NOT_DISABLED', 'RECEIPTS_PRESENT', 'QUEUE_NOT_EMPTY', 'TARGET_IDENTITY_MISMATCH',
    'NEON_CONNECTION_HOST_MISMATCH', 'NEON_VERIFIED_TLS_REQUIRED', 'NEON_ROUTING_OVERRIDE_REFUSED',
    'NEON_SERVER_IDENTITY_MISSING', 'NEON_SERVER_IDENTITY_MISMATCH', 'LOGIN_OR_QUEUE_FAILED',
    'LOGIN_CONNECT_FAILED', 'LOGIN_AUTH_FAILED', 'LOGIN_TLS_FAILED', 'LOGIN_SESSION_FAILED',
    'LOGIN_QUEUE_QUERY_FAILED', 'LOGIN_QUEUE_NOT_EMPTY', 'POST_CHECK_DRIFT',
    'HOST_IDENTITY', 'FILE_DRIFT', 'UNIT_DRIFT', 'BASE_DRIFT', 'DROP_DRIFT', 'ROUTE_DRIFT',
    'PIN_DRIFT', 'ENV_DRIFT', 'DSN_DRIFT', 'LIVE_ENV_DRIFT', 'LIVE_PROCESS_DRIFT',
    'TIMEOUT_ERROR', 'OS_ERROR', 'KEY_ERROR', 'TYPE_ERROR', 'VALUE_ERROR',
    'DB_CONNECTION_ERROR', 'DB_PRIVILEGE_ERROR', 'DB_TRANSACTION_ERROR', 'DB_QUERY_ERROR',
    'DB_DRIVER_ERROR'))


def failure_code(exc):
    code = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
    if code in SAFE_CODES:
        return code
    # SQLSTATE is compared to fixed categories; no server detail is emitted.
    state = getattr(exc, 'sqlstate', None)
    if state in ('08000','08001','08003','08004','08006','08007','08P01','28P01'):
        return 'DB_CONNECTION_ERROR'
    if state == '42501': return 'DB_PRIVILEGE_ERROR'
    if state in ('25000','25001','25006','25P01','25P02'): return 'DB_TRANSACTION_ERROR'
    if state in ('42601','42703','42P01','42883','57014'): return 'DB_QUERY_ERROR'
    driver = sys.modules.get('psycopg')
    if driver is not None and isinstance(exc, driver.Error): return 'DB_DRIVER_ERROR'
    for cls, label in ((TimeoutError,'TIMEOUT_ERROR'), (OSError,'OS_ERROR'),
                       (KeyError,'KEY_ERROR'), (TypeError,'TYPE_ERROR'), (ValueError,'VALUE_ERROR')):
        if isinstance(exc, cls): return label
    return 'REFUSED'


def main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope):
    diagnostic = dict(phase='request', channel=None)
    try:
        return _main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope, diagnostic)
    except BaseException as exc:
        # Only fixed vocabulary goes onto the authenticated pipe. Exception text,
        # tracebacks, SQL, URLs and private snapshot values never leave this host.
        code = failure_code(exc)
        channel = diagnostic['channel']
        if channel is not None:
            try:
                channel.send(dict(kind='NATIVE_REHEARSAL_REFUSED', binding=binding,
                    request_digest=request_digest, phase=diagnostic['phase'], code=code))
            except BaseException:
                pass  # A failed pipe is never reopened or granted a fresh deadline.
        raise SystemExit(2) from None


def _main(source, run_id, attempt, request_digest, binding, wheel_digest, envelope, diagnostic):
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
    diagnostic.update(channel=channel, phase='run_authentication')
    run = RehearsalRunBinding(source, run_id, attempt, API(envelope['token']))
    run.assert_running()
    require(type(envelope['job_id']) is int and run.job_id == envelope['job_id'], 'REHEARSAL_JOB_CHANGED')
    diagnostic['phase'] = 'supervisor'
    supervisor = SelfSupervisor(source, run)
    supervisor.assert_exclusive()
    from ops import oracle_light_active_hold_attest as hold
    diagnostic['phase'] = 'hold'
    before = hold.attest()
    diagnostic['phase'] = 'driver'
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
        diagnostic['phase'] = 'owner_snapshot'
        report = owner.observe(psycopg.connect, envelope['credential'])
        connections = OwnedConnections(connect, target, approved_hold=before)
        plan = request['plan']
        workflows = WorkflowAPI(envelope['token'], plan, digest(plan))
        diagnostic['phase'] = 'workflow_state'
        for row in plan['workflows']:
            require(workflows.get_workflow(row['id']) == row, 'REHEARSAL_WORKFLOW_CHANGED')
        diagnostic['phase'] = 'workflow_drain'
        WorkflowDrain(run.api, plan, digest(plan)).assert_drained()
        diagnostic['phase'] = 'backend_drain'
        connections.assert_drained()
        diagnostic['phase'] = 'hold_recheck'
        require(hold.attest() == before, 'REHEARSAL_HOLD_CHANGED')
        run.assert_running()
        diagnostic['phase'] = 'checkpoint'
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
            diagnostic['phase'] = 'restore'
            restored = snapshot.restore(data, checkpoint.sha(data), scope_digest, parent)
            require(snapshot.capture(restored/'operation', restored/'pause') == data,
                    'REHEARSAL_RESTORE')
        diagnostic['phase'] = 'backend_drain'
        connections.assert_drained()
        diagnostic['phase'] = 'final_drain'
        require(hold.attest() == before, 'REHEARSAL_FINAL_HOLD')
        supervisor.assert_alive()
        run.assert_running()
        diagnostic['phase'] = 'completion'
        channel.send(dict(kind='NATIVE_REHEARSAL_COMPLETE', binding=binding,
            request_digest=request_digest, scope_digest=scope_digest, head_digest=head,
            sequence=store.sequence, elapsed_ms=int((time.monotonic()-started)*1000),
            snapshot_digest=report['snapshot_digest'], timing_is_estimate=True,
            production_mutations=False, snapshot_approved=False,supervisor=supervisor.record))
