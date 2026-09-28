"""Incident-only observation against unchanged source8bbc; never a stage launcher.

Run only under an independently approved, privileged, bounded transport. This
file does not supply that transport or authorize its own execution. See runbook.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import types

SOURCE = '8bbc1d61010ef70c3fca02b5151ac86fce02a144'
BUNDLE = '4ff5fabec410197437c130d3b56980451c32f90a5fffb9e468c211661f7337da'
REQUEST = '01112f4599bd21c72e29dc5f890d9d538d39fa7976e17c26b674b258a40f043a'
SCOPE = 'c0795e9e1533c3baf554a0d785b303180d8bf6771d07e6d98e5f67f0b96ef542'
AFTER = '51e06db1da9d9b768e50cb18de89a75fce0ded7b33a034bb3852c6ecb8f38d12'
FAILED_RUN = dict(run_id=36388443245, attempt=1, job_id=108818853655)
PHASE = 'input'
CODES = frozenset(('PROBE_SOURCE', 'PROBE_ISOLATION', 'PROBE_INPUT', 'PROBE_HOST',
    'PROBE_REQUEST', 'PROBE_HOLD', 'PROBE_AFTER', 'PROBE_READ_ONLY', 'PROBE_TIMEOUT',
    'WORKFLOW_SOURCE_CHANGED', 'WORKFLOW_NOT_DRAINED', 'INSPECT_FAILED_RUN',
    'INSPECT_FAILED_JOB', 'DATABASE_NOT_DRAINED', 'DATABASE_ACTIVITY_INCOMPLETE',
    'DATABASE_PREPARED_TRANSACTION_PRESENT', 'DRAIN_HOLD_CHANGED',
    'DRAIN_PRIOR_HOST_ACTIVE', 'DRAIN_PRIOR_CGROUP_ACTIVE', 'DRAIN_PRIOR_CGROUP_PRESENT',
    'API_TRANSPORT_FAILED', 'API_RESPONSE_INCOMPLETE', 'DRAIN_BACKEND_IDENTITY',
    'DRAIN_OWNED_BACKEND_MISSING', 'DRAIN_OWNED_BACKEND_CHANGED',
    'DRAIN_OWNED_CONNECTION_REQUIRED', 'DRAIN_OWNED_BACKEND_LOST',
    'CONFIG_NOT_DISABLED', 'RECEIPTS_PRESENT', 'QUEUE_NOT_EMPTY',
    'TARGET_IDENTITY_MISMATCH', 'NEON_CONNECTION_HOST_MISMATCH',
    'NEON_VERIFIED_TLS_REQUIRED', 'NEON_ROUTING_OVERRIDE_REFUSED',
    'NEON_SERVER_IDENTITY_MISSING', 'NEON_SERVER_IDENTITY_MISMATCH',
    'RUNTIME_OTHER_SUPERVISOR_ACTIVE', 'RUNTIME_OTHER_CGROUP_ACTIVE',
    'RUNTIME_UNLISTED_CGROUP', 'RUNTIME_SELF_RUN', 'OWNER_DRIVER_ORIGIN', 'OWNER_DRIVER_VERSION', 'OWNER_DRIVER_SUBMODULE_ORIGIN'))


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def code(exc):
    # No arbitrary exception strings, SQL text, DSN, token or private identity.
    value = exc.args[0] if len(exc.args) == 1 and type(exc.args[0]) is str else None
    if value in CODES:
        return value
    if getattr(exc, 'sqlstate', None) == '28P01':
        return 'DB_AUTHENTICATION'
    if isinstance(exc, TimeoutError):
        return 'TIMEOUT'
    return 'UNCLASSIFIED'


@contextmanager
def source_runtime(payload):
    # Authenticate all bytes BEFORE executing even the source decoder.
    require(type(payload) is bytes and digest(payload) == BUNDLE, 'PROBE_SOURCE')
    require(not any(n.split('.')[0] in ('ops', 'database', 'psycopg',
                'psycopg_binary', 'typing_extensions') for n in sys.modules), 'PROBE_ISOLATION')
    value = json.loads(payload)
    decoder = types.ModuleType('_incident_source_decoder')
    raw = base64.b64decode(value['files']['ops/native_maintenance_bundle.py'], validate=True)
    exec(compile(raw, '<verified-source-decoder>', 'exec'), decoder.__dict__)
    with decoder.extracted(payload, SOURCE, BUNDLE) as root:
        sys.path.insert(0, str(root))
        try:
            yield root
        finally:
            sys.path.remove(str(root))


def source_origins(root):
    for name, module in tuple(sys.modules.items()):
        if name.split('.')[0] in ('ops', 'database'):
            path = getattr(module, '__file__', None)
            require(path is not None and Path(path).resolve().is_relative_to(root), 'PROBE_ISOLATION')


@contextmanager
def readonly_connection(connect, kwargs):
    with connect(**kwargs, autocommit=True) as conn:
        conn.read_only = True
        # Owner identity rejects nonempty libpq options; set only session state.
        # This fixed SET is the first SQL. All original queries follow it.
        conn.execute('SET default_transaction_read_only=on')
        require(conn.execute('SHOW default_transaction_read_only').fetchone() == ('on',),
                'PROBE_READ_ONLY')
        require(conn.execute('SHOW transaction_read_only').fetchone() == ('on',),
                'PROBE_READ_ONLY')
        yield conn


def observe(connect, credential, token, root):
    global PHASE
    from database import native_cli_permission_engine as engine
    from ops import native_maintenance_coordination as coordination
    from ops import oracle_light_active_hold_attest as hold
    from ops.native_maintenance_owner_attest import parameters
    from ops.native_maintenance_stage_request import AcceptedRequest, read_request
    from ops.native_maintenance_stage_inspect import inspection_packet, failed_run
    from ops.native_maintenance_run_guard import PersistentAPI
    from ops.native_maintenance_workflow_api import Transport, source_matches
    from ops.native_maintenance_workflow_pause import digest as canonical_digest
    from ops.native_permission_hold_guard import EXPECTED_TARGET

    source_origins(root)
    PHASE = 'request'
    raw = read_request(REQUEST)
    request = AcceptedRequest(raw, REQUEST, SOURCE)
    packet = inspection_packet(request, FAILED_RUN)
    require(packet['stage'] == 'restore' and packet['expected_outcome'] == 'AFTER'
            and canonical_digest(packet['scope']) == SCOPE
            and packet['scope']['target'] == EXPECTED_TARGET, 'PROBE_REQUEST')
    accepted_hold = hold.HoldIdentity(**packet['scope']['hold'])
    target = engine.Target(**{**EXPECTED_TARGET,
                            'neon': engine.NeonBinding(**EXPECTED_TARGET['neon'])})
    kwargs = parameters(credential)
    def connect_ro():
        return readonly_connection(connect, kwargs)
    evidence = dict(failed_run=FAILED_RUN, failed_source=SOURCE)
    with PersistentAPI(token) as api:
        transport = Transport(token, read_api=api)
        PHASE = 'source_and_failed_run'
        source_matches(transport, SOURCE)
        failed_run(api, evidence)
        PHASE = 'hold'
        require(hold.attest() == accepted_hold, 'PROBE_HOLD')
        PHASE = 'workflow_drain'
        coordination.WorkflowDrain(api, packet['plan'], canonical_digest(packet['plan'])).assert_drained()
        PHASE = 'prior_host_drain'
        priors = [row['supervisor'] for row in packet['prior_units']]
        coordination.PriorSupervisors(priors, canonical_digest(priors)).assert_drained()
        PHASE = 'owner_backend_drain'
        coordination.OwnedConnections(connect_ro, target, approved_hold=accepted_hold).assert_drained()
        PHASE = 'after_snapshot'
        with connect_ro() as conn, conn.transaction():
            require(conn.execute('SHOW transaction_read_only').fetchone() == ('on',),
                    'PROBE_READ_ONLY')
            conn.execute("SET LOCAL statement_timeout='5s'")
            conn.execute("SET LOCAL lock_timeout='1s'")
            state = engine.snapshot(conn, target)
            engine.dormant(state)
            require(engine.digest(state) == AFTER, 'PROBE_AFTER')
        PHASE = 'continuity'
        require(read_request(REQUEST) == raw, 'PROBE_REQUEST')
        require(hold.attest() == accepted_hold, 'PROBE_HOLD')
        source_matches(transport, SOURCE)
        failed_run(api, evidence)
        source_origins(root)


def execute(payload, wheels, envelope, supervised_run):
    global PHASE
    require(set(envelope) == {'credential', 'token'} and
            all(type(envelope[k]) is str and 0 < len(envelope[k]) <= limit
                for k, limit in (('credential', 8192), ('token', 4096))), 'PROBE_INPUT')
    require(type(supervised_run) is tuple and len(supervised_run) == 2
            and all(type(n) is int and n > 0 for n in supervised_run), 'PROBE_INPUT')
    PHASE = 'source_bundle'
    with source_runtime(payload) as root:
        from ops.native_maintenance_owner_host import loaded_runtime
        PHASE = 'driver'
        # Original decode pins every wheel hash and verifies the installed tree.
        # No install, repair, pip, GRANT, claim or stage call is exposed here.
        with loaded_runtime(wheels) as (driver, _):
            from ops.native_maintenance_supervisor import StageSupervisor
            PHASE = 'supervisor'
            # Real diagnostic run identity, used only for the PID1/cgroup check.
            # This is not a RunBinding and cannot admit a maintenance stage.
            identity = types.SimpleNamespace(run_id=supervised_run[0], attempt=supervised_run[1])
            supervisor = StageSupervisor(SOURCE, identity)
            supervisor.assert_exclusive()
            observe(driver.connect, envelope['credential'], envelope['token'], root)
            PHASE = 'supervisor_final'
            supervisor.assert_exclusive()
        source_origins(root)
    PHASE = 'complete'


def entrypoint():
    global PHASE
    result = dict(audit='LIGHT_AFTER_READ_ONLY_PROBE', source=SOURCE,
                  production_mutations=False, resume_authorized=False,
                  historical_cause_proven=False)
    def expired(*_):
        raise RuntimeError('PROBE_TIMEOUT')
    try:
        require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode,
                'PROBE_ISOLATION')
        require(os.getuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'PROBE_HOST')
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(180)
        parser = argparse.ArgumentParser()
        parser.add_argument('--source-bundle', required=True)
        parser.add_argument('--driver-bundle', required=True)
        parser.add_argument('--run-id', required=True, type=int)
        parser.add_argument('--attempt', required=True, type=int)
        args = parser.parse_args()
        with open(args.source_bundle, 'rb') as stream:
            payload = stream.read(2 * 1024 * 1024 + 1)
        with open(args.driver_bundle, 'rb') as stream:
            wheels = stream.read(10 * 1024 * 1024 + 1)
        envelope = json.loads(sys.stdin.buffer.read(16385))
        execute(payload, wheels, envelope, (args.run_id, args.attempt))
        result.update(status='PASS', phase=PHASE)
        status = 0
    except BaseException as exc:
        result.update(status='REFUSED', phase=PHASE, code=code(exc))
        status = 2
    finally:
        signal.alarm(0)
    print(json.dumps(result, sort_keys=True))
    return status


if __name__ == '__main__':
    sys.exit(entrypoint())
