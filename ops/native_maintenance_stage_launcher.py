"""Fixed SSH/PID1 launcher with separate production and read-only commands.

Private request bytes travel only on pinned SSH and private OCI. Dispatch inputs
carry their independently accepted digest. No automatic stage chaining/retry.
"""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import threading
from contextlib import contextmanager

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_driver as driver
from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_checkpoint_transport as rpc
from ops.native_maintenance_readonly_transport import stop_group
from ops.native_maintenance_run_guard import API, StageRunBinding, RehearsalRunBinding, REPOSITORY
from ops.native_maintenance_stage_request import AcceptedRequest, MAX_REQUEST, first_install_intent
from ops.native_maintenance_store_runner import HOST, loader, source_check
from ops.native_maintenance_workflow_pause import require, encoded, digest, unique
from ops.oracle_autopilot_source_preflight import connection_parameters

PHASE = 'startup'
RUN_STARTED = None
PROFILE = None


class TimingProfile:
    """Bounded runner observations, never authority or a host-duration estimate.

    Labels are code-owned. Inputs, results and exception text are never retained.
    Nested/concurrent durations overlap and must not be summed as wall time.
    """
    LABELS = frozenset(('github_get', 'oci_read', 'oci_write', 'rpc_wait',
                       'rpc_store', 'rpc_unit', 'source_check'))

    def __init__(self):
        self.rows = {}
        self.exchange_rows = {}
        self.lock = threading.Lock()
        self.incomplete = False

    @contextmanager
    def observe(self, label, *, host_exchange=False):
        if label not in self.LABELS:
            raise ValueError('TIMING_LABEL_INVALID')
        try:
            start = time.monotonic()
        except Exception:
            self.incomplete = True
            yield
            return
        try:
            yield
        finally:
            # Measurement must not mask the original operation's refusal.
            try:
                elapsed = max(0, min(1000000, int((time.monotonic()-start)*1000)))
                with self.lock:
                    for rows in ([self.rows, self.exchange_rows] if host_exchange else [self.rows]):
                        row = rows.setdefault(label, {'calls': 0, 'total_ms': 0, 'max_ms': 0})
                        row['calls'] = min(1000000, row['calls']+1)
                        row['total_ms'] = min(1000000000, row['total_ms']+elapsed)
                        row['max_ms'] = max(row['max_ms'], elapsed)
            except Exception:
                self.incomplete = True

    def report(self):
        with self.lock:
            return {'version': 1, 'runner_only': True, 'overlapping_durations': True,
                    'incomplete': self.incomplete,
                    'rpc_budget_seconds': 60,
                    'operations': {k: dict(v) for k, v in sorted(self.rows.items())},
                    'host_exchange_operations': {k: dict(v) for k, v in sorted(self.exchange_rows.items())}}


@contextmanager
def measured(label):
    if PROFILE is None:
        yield
    else:
        with PROFILE.observe(label, host_exchange=PHASE == 'host_exchange'):
            yield


class MeasuredAPI(API):
    def get(self, suffix):
        with measured('github_get'):
            return super().get(suffix)


class MeasuredStore(adapter.OCIJournalStore):
    def _call(self, method, *args, **kwargs):
        # Preserve the adapter's exact method, arguments, no-retry and guards.
        with measured('oci_write' if method == 'put_object' else 'oci_read'):
            return super()._call(method, *args, **kwargs)


SAFE_REFUSALS = frozenset(('RUN_BINDING_EXPIRED','RUN_BINDING_ALREADY_FAILED',
    'RUN_BINDING_EXPIRED_OR_UNOBSERVED','RUN_NOT_RUNNING','JOB_NOT_RUNNING','MAIN_CHANGED',
    'WORKFLOW_CONTRACT_CHANGED','RPC_EXPIRED','RPC_TIMEOUT','RPC_EOF','RPC_UNAVAILABLE',
    'CANDIDATE_HOST_REFUSED','LAUNCHER_HOST_REFUSED','LAUNCHER_REQUEST_READ'))

def failure_code(exc):
    code = exc.args[0] if len(exc.args)==1 and type(exc.args[0]) is str else None
    return code if code in SAFE_REFUSALS else ('PROCESS_TIMEOUT' if isinstance(exc,subprocess.TimeoutExpired) else 'REFUSED')

def timing():
    result = dict(binding_elapsed_ms=None if RUN_STARTED is None else int((time.monotonic()-RUN_STARTED)*1000),
                  binding_budget_seconds=100)
    if PROFILE is not None:
        try:
            result['timing_profile'] = PROFILE.report()
        except Exception:
            result['timing_profile'] = {'version': 1, 'runner_only': True, 'incomplete': True}
    return result
REQUEST_PREFIX = 'native-journal/stage-requests-v1/'


def context(mode):
    require(mode in ('stage', 'rehearsal'), 'LAUNCHER_MODE')
    cls = StageRunBinding if mode == 'stage' else RehearsalRunBinding
    source, accepted = os.environ.get('EXPECTED_MAIN'), os.environ.get('ACCEPTED_REQUEST_DIGEST')
    require(bundle.identifier(source, 40) and bundle.identifier(accepted, 64), 'LAUNCHER_INPUT')
    require(os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
            and os.environ.get('GITHUB_REF') == 'refs/heads/main'
            and os.environ.get('GITHUB_SHA') == source
            and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch'
            and os.environ.get('GITHUB_ACTOR') == 'olegmed1-art'
            and os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art'
            and os.environ.get('GITHUB_JOB') == cls.job_name
            and os.environ.get('GITHUB_WORKFLOW_REF') == REPOSITORY+'/'+cls.workflow+'@refs/heads/main'
            and os.environ.get('GITHUB_WORKFLOW_SHA') == source, 'LAUNCHER_CONTEXT')
    return cls, source, accepted


def source_guard():
    with measured('source_check'):
        source_check(os.environ.get('EXPECTED_MAIN'))


def bootstrap(repo, source, source_digest, wheel_digest, run_id, attempt, accepted, binding, mode):
    require(mode in ('fetch', 'drain', 'first_install', 'candidate', 'inspect', 'stage', 'rehearsal') and bundle.identifier(accepted,64)
            and bundle.identifier(source,40) and bundle.identifier(source_digest,64)
            and bundle.identifier(wheel_digest,64) and bundle.identifier(binding,64)
            and type(run_id) is int and run_id > 0 and type(attempt) is int and attempt > 0,
            'LAUNCHER_BOOTSTRAP_IDENTITY')
    lifetime = bundle.git(repo, 'show', source+':ops/native_maintenance_lifetime.py')
    decoder = bundle.git(repo, 'show', source+':ops/native_maintenance_bundle.py')
    require(0 < len(lifetime) <= 32768 and 0 < len(decoder) <= 32768, 'LAUNCHER_BOOTSTRAP_SIZE')
    code = ('import base64,types,sys,json,os\n'+loader('bundle',decoder)
        +'def read_exact(size):\n data=bytearray()\n while len(data)<size:\n'
        +"  part=os.read(0,min(65536,size-len(data)))\n  bundle.check(part,'LAUNCHER_EOF')\n"
        +'  data.extend(part)\n return bytes(data)\n'
        +"size=int.from_bytes(read_exact(4),'big')\nbundle.check(0<size<=23*1024*1024,'LAUNCHER_FRAME')\n"
        +'raw=read_exact(size)\nvalue=json.loads(raw,object_pairs_hook=bundle.unique)\n'
        +"bundle.check(type(value) is dict and set(value)=={'source','envelope'} and bundle.canonical(value)==raw,'LAUNCHER_FRAME_SCHEMA')\n"
        +"payload=base64.b64decode(value['source'],validate=True)\n"
        +'with bundle.extracted(payload,'+repr(source)+','+repr(source_digest)+') as root:\n'
        +' sys.path.insert(0,str(root))\n')
    if mode == 'fetch':
        code += (" from ops.native_maintenance_stage_request import resolve_request\n"
                 +" bundle.check(value['envelope']=={},'LAUNCHER_FETCH_SCHEMA')\n"
                 +' print(base64.b64encode(resolve_request('+repr(accepted)+','+repr(source)+')).decode(),flush=True)\n')
    elif mode == 'first_install':
        code += (" from ops.native_maintenance_stage_request import first_install\n"
                 +" bundle.check(value['envelope']=={},'LAUNCHER_PROVISION_SCHEMA')\n"
                 +' first_install('+repr(source)+','+repr(accepted)+')\n'
                 +" print('NATIVE_REQUEST_STORE_PROVISIONED',flush=True)\n")
    elif mode in ('candidate','inspect'):
        module='native_maintenance_grant_candidate' if mode=='candidate' else 'native_maintenance_stage_inspect'
        code += (" from ops."+module+" import main\n"
                 + ' main('+','.join(map(repr,(source,run_id,attempt,accepted,wheel_digest)))+",value['envelope'])\n")
    elif mode == 'drain':
        code += (" from ops.native_maintenance_supervisor import PriorSupervisors\n"
            +" from ops.native_maintenance_workflow_pause import digest\n"
            +" records=value['envelope']\n PriorSupervisors(records,digest(records)).assert_drained()\n"
            +" print('NATIVE_PRIOR_HOST_DRAINED',flush=True)\n")
    else:
        module = 'native_maintenance_stage_host' if mode == 'stage' else 'native_maintenance_stage_rehearsal'
        code += (' from ops.'+module+' import main\n main('+','.join(map(repr,
            (source,run_id,attempt,accepted,binding,wheel_digest)))+",value['envelope'])\n")
    outer = ('import base64,types\n'+loader('lifetime',lifetime)
        +'try:\n result=lifetime.managed('+repr(code)+','+repr(base64.b64encode(lifetime).decode())
        +','+repr(source)+','+repr(str(run_id)+'-'+str(attempt))+')\n'
        +'except BaseException:\n result=2\nraise SystemExit(result)\n')
    require(len(outer.encode()) <= 98304, 'LAUNCHER_BOOTSTRAP_SIZE')
    return outer


def ssh_command(key, known_hosts, code):
    return ['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known_hosts,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1',HOST,
        shlex.join(['sudo','-n','/usr/bin/python3','-I','-B','-S','-c',code])]


def frame(source_payload, envelope):
    return dict(source=base64.b64encode(source_payload).decode('ascii'), envelope=envelope)


def fetch_request(command, source_payload, accepted):
    raw = encoded(frame(source_payload, {}))
    source_guard()
    result = subprocess.run(command, input=len(raw).to_bytes(4,'big')+raw,
        capture_output=True, timeout=115, env={'PATH':'/usr/bin:/bin'})
    require(result.returncode == 0 and 0 < len(result.stdout) <= 4*((MAX_REQUEST+2)//3)+1,
            'LAUNCHER_REQUEST_READ')
    data = base64.b64decode(result.stdout.rstrip(b'\n'), validate=True)
    require(base64.b64encode(data)+b'\n' == result.stdout and bundle.digest(data) == accepted,
            'LAUNCHER_REQUEST_DIGEST')
    source_guard()
    return data


def verify_host_exit(command, source_payload, supervisor):
    """Independent source-pinned host observation after the original SSH exits."""
    raw = encoded(frame(source_payload,[supervisor]))
    source_guard()
    result = subprocess.run(command,input=len(raw).to_bytes(4,'big')+raw,
        capture_output=True,timeout=115,env={'PATH':'/usr/bin:/bin'})
    require(result.returncode == 0 and result.stdout == b'NATIVE_PRIOR_HOST_DRAINED\n',
            'LAUNCHER_PRIOR_HOST_NOT_DRAINED')
    source_guard()


def oci_client():
    import oci
    from ops.oci_light_access_audit import scalar
    from oci_storage_audit import main as audit
    _, _, inventory = audit()
    require(inventory['compartments'] == 1 and inventory['allocated_gb'] <= 100, 'LAUNCHER_INVENTORY')
    config = {k:scalar(os.environ[e],k) for k,e in (
        ('user','OCI_USER'),('tenancy','OCI_TENANCY'),('fingerprint','OCI_FINGERPRINT'),('region','OCI_REGION'))}
    require(config['tenancy'] == adapter.TENANCY and config['region'] == 'eu-frankfurt-1', 'LAUNCHER_OCI_TARGET')
    config['key_content'] = os.environ['OCI_KEY'].replace('\\r','').replace('\\n','\n')
    client = oci.object_storage.ObjectStorageClient(config,timeout=(5,10),retry_strategy=oci.retry.NoneRetryStrategy())
    namespace = client.get_namespace(compartment_id=adapter.TENANCY,retry_strategy=oci.retry.NoneRetryStrategy()).data
    return client, namespace


def retain_request(store, raw, accepted):
    """Private recovery copy only; neither existence nor hash grants approval."""
    require(bundle.identifier(accepted,64) and bundle.digest(raw) == accepted, 'LAUNCHER_REQUEST_DIGEST')
    key = REQUEST_PREFIX+accepted+'.json'
    try:
        store.assert_private()
        existing = store._read(key,MAX_REQUEST)
        if existing is None:
            store._budget(len(raw))
            store._put(key,raw)
        else:
            require(existing[0] == raw, 'LAUNCHER_REQUEST_CONFLICT')
        require(store._read(key,MAX_REQUEST)[0] == raw, 'LAUNCHER_REQUEST_BACKUP')
        store.assert_private()
        store.guard()
    except BaseException:
        store.failed = True
        raise


def restore_assets(store, request, source_payload):
    from ops import native_maintenance_recovery_assets as assets
    a = request.value['assets']
    ids = (request.source,a['source_digest'],a['manifest_digest'],a['baseline_digest'])
    require(bundle.digest(source_payload) == a['source_digest'], 'LAUNCHER_SOURCE_ASSETS')
    data = store.read_assets(a['envelope_digest'],*ids)
    with tempfile.TemporaryDirectory(prefix='native-stage-assets-') as directory:
        restored = assets.restore(data,a['envelope_digest'],*ids,Path(directory))
        require((restored/'source.json').read_bytes() == source_payload, 'LAUNCHER_RESTORED_SOURCE')
        return (restored/'manifest.json').read_bytes()


def verify_prior(store, packet):
    from ops.native_maintenance_stage_unit import read_accepted
    for row in packet.prior:
        expected = {k:row[k] for k in ('source','scope_digest','stage','run')}
        require(read_accepted(store,expected,digest(row)) == encoded(row), 'LAUNCHER_PRIOR_UNIT')
    if packet.stage != 'prepare':
        checkpoint.accepted_latest(store,packet.scope_digest,packet.value['accepted_head_digest'])


def candidate_step(repo, source, accepted, raw, candidate, run, reader, client, namespace,
                   source_payload, wheels, args, key, known_hosts):
    """Private candidate return over SSH; public output contains digests only."""
    from types import SimpleNamespace
    from ops.native_maintenance_stage_unit import read_accepted
    manifest = restore_assets(reader,SimpleNamespace(source=source,value=candidate),source_payload)
    units=[]
    for ref in candidate['prior_units']:
        expected=dict(source=source,scope_digest=candidate['scope_digest'],stage=ref['stage'],run=ref['run'])
        units.append(base64.b64encode(read_accepted(reader,expected,ref['digest'])).decode())
    envelope=dict(request=base64.b64encode(raw).decode(),manifest=base64.b64encode(manifest).decode(),
                  units=units,token=os.environ['GH_TOKEN'],job_id=run.job_id,driver=base64.b64encode(wheels).decode())
    data=encoded(frame(source_payload,envelope))
    run.assert_running(); source_guard()
    result=subprocess.run(ssh_command(key,known_hosts,bootstrap(*args,'candidate')),
        input=len(data).to_bytes(4,'big')+data,capture_output=True,timeout=115,env={'PATH':'/usr/bin:/bin'})
    require(result.returncode==0 and 0<len(result.stdout)<=512*1024,'CANDIDATE_HOST_REFUSED')
    value=json.loads(result.stdout,object_pairs_hook=unique)
    require(type(value) is dict and set(value)=={'report','request'},'CANDIDATE_HOST_SCHEMA')
    report=value['report']
    require(type(report) is dict and set(report)=={'audit','source','scope_digest','plan_digest','before_digest',
        'assets','stage','request_digest','approved','production_mutations'}
        and report['audit']=='NATIVE_GRANT_REQUEST_CANDIDATE' and report['source']==source
        and report['assets']==candidate['assets'] and report['plan_digest']==digest(candidate['plan'])
        and report['stage']==candidate['stage'] and report['approved'] is False
        and report['production_mutations'] is False
        and all(bundle.identifier(report[k],64) for k in ('scope_digest','plan_digest','before_digest'))
        and (candidate['scope_digest'] is None or report['scope_digest']==candidate['scope_digest']),
        'CANDIDATE_HOST_REPORT')
    run.assert_running(); source_guard()
    if candidate['agreement'] is None:
        require(value['request'] is None and report['request_digest'] is None,'CANDIDATE_PREVIEW_MUTATED')
    else:
        request_raw=base64.b64decode(value['request'],validate=True)
        request=AcceptedRequest(request_raw,report['request_digest'],source)
        packet=request.value['packet']
        require(request.value['request_id']==candidate['request_id'] and request.value['assets']==candidate['assets']
            and packet['stage']==candidate['stage'] and digest(packet['scope'])==report['scope_digest']
            and packet['plan']==candidate['plan'] and packet['agreement']==candidate['agreement']
            and packet['accepted_head_digest']==candidate['accepted_head_digest']
            and packet['expected_outcome']==candidate['expected_outcome']
            and [digest(row) for row in packet['prior_units']]==[r['digest'] for r in candidate['prior_units']],
            'CANDIDATE_REQUEST_CHANGED')
        store=MeasuredStore(client,namespace,run.assert_running)
        retain_request(store,request_raw,report['request_digest'])
    run.assert_running(); source_guard()
    print(json.dumps({**report,**timing()},sort_keys=True))


def inspect_step(source,accepted,raw,v,run,reader,source_payload,wheels,args,key,known_hosts):
    from ops import native_maintenance_stage_inspect as inspection
    inspection.failed_run(run.api,v)
    envelope=dict(request=base64.b64encode(raw).decode(),token=os.environ['GH_TOKEN'],
                  job_id=run.job_id,driver=base64.b64encode(wheels).decode())
    data=encoded(frame(source_payload,envelope))
    command=ssh_command(key,known_hosts,bootstrap(*args,'inspect'))
    def read_local():
        run.assert_running();source_guard()
        result=subprocess.run(command,input=len(data).to_bytes(4,'big')+data,capture_output=True,
            timeout=115,env={'PATH':'/usr/bin:/bin'})
        require(result.returncode==0 and 0<len(result.stdout)<=inspection.LIMIT+1,'INSPECT_HOST_REFUSED')
        value=json.loads(result.stdout,object_pairs_hook=unique)
        require(encoded(value)+b'\n'==result.stdout,'INSPECT_HOST_CANONICAL')
        run.assert_running();source_guard()
        return value
    observed=read_local()
    report=inspection.compare(observed,reader,v)
    require(read_local()==observed,'INSPECT_LOCAL_CHANGED')
    # Observe again after the second local read; no inferred head is accepted.
    require(inspection.compare(observed,reader,v)==report,'INSPECT_REMOTE_CHANGED')
    inspection.failed_run(run.api,v);run.assert_running();source_guard()
    print(json.dumps(dict(report,source=source,failed_source=v['failed_source'],
        failed_request_digest=v['failed_request_digest'],failed_run=v['failed_run'],**timing()),sort_keys=True))


def main(mode):
    global PHASE, RUN_STARTED, PROFILE
    PROFILE = TimingProfile()
    require(len(sys.argv) == 5 and sys.argv[1] == mode, 'LAUNCHER_ARGS')
    cls, source, accepted = context(mode)
    source_guard()
    key, known_hosts, wheel_directory = sys.argv[2:]
    repo = Path(__file__).resolve().parents[1]
    source_payload, wheels = bundle.build(repo,source), driver.build(wheel_directory)
    run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
    binding = digest(dict(version=1,mode=mode,source=source,request_digest=accepted,run_id=run_id,attempt=attempt))
    args = (repo,source,bundle.digest(source_payload),bundle.digest(wheels),run_id,attempt,accepted,binding)
    action = os.environ.get('REQUEST_STORE_ACTION','rehearse')
    require(action in ('rehearse','first_install','candidate','inspect') and (mode == 'rehearsal' or action == 'rehearse'),
            'LAUNCHER_PROVISION_MODE')
    if action == 'first_install':
        require(bundle.digest(first_install_intent(source)) == accepted, 'LAUNCHER_PROVISION_INTENT')
    # This is the one nonrenewing run binding for the entire launcher.  Fetch
    # can import an accepted request leaf, so authenticate before that host I/O.
    RUN_STARTED = time.monotonic()
    run = cls(source,run_id,attempt,MeasuredAPI(os.environ['GH_TOKEN']),launcher=True)
    run.assert_running()
    if action == 'first_install':
        PHASE = 'explicit_first_install'
        data = encoded(frame(source_payload,{}))
        command = ssh_command(key,known_hosts,bootstrap(*args,'first_install'))
        result = subprocess.run(command,input=len(data).to_bytes(4,'big')+data,capture_output=True,
                                timeout=115,env={'PATH':'/usr/bin:/bin'})
        require(result.returncode == 0 and result.stdout == b'NATIVE_REQUEST_STORE_PROVISIONED\n',
                'LAUNCHER_PROVISION_REFUSED')
        source_guard()
        run.assert_running()
        print(json.dumps(dict(audit='NATIVE_REQUEST_STORE_FIRST_INSTALL_PASS',source_sha=source,
            accepted_intent_digest=accepted,run_id=run_id,attempt=attempt,job_id=run.job_id,
            production_sql_mutations=False,stage_authority=False),sort_keys=True))
        return
    PHASE = 'request_readback'
    raw = fetch_request(ssh_command(key,known_hosts,bootstrap(*args,'fetch')),source_payload,accepted)
    request = manifest = packet = None
    if mode == 'stage':
        request = AcceptedRequest(raw,accepted,source)
    elif action in ('candidate','inspect'):
        if action=='candidate':
            from ops.native_maintenance_grant_candidate import request_value
        else:
            from ops.native_maintenance_stage_inspect import input_value as request_value
        candidate = request_value(raw,accepted,source)
    else:
        from ops.native_maintenance_stage_rehearsal import request_value
        request_value(raw,accepted,source)
    PHASE = 'private_recovery_readback'
    client, namespace = oci_client()
    # Preparatory operations are read-only. No run lease is renewed after start.
    def read_only(): raise RuntimeError('LAUNCHER_READ_ONLY_STORE')
    reader = MeasuredStore(client,namespace,read_only)
    if action == 'inspect':
        os.environ.pop('NATIVE_OWNER_DATABASE_URL',None)
        inspect_step(source,accepted,raw,candidate,run,reader,source_payload,wheels,args,key,known_hosts)
        return
    if action == 'candidate':
        candidate_step(repo,source,accepted,raw,candidate,run,reader,client,namespace,source_payload,wheels,args,key,known_hosts)
        return
    if request is not None:
        manifest = restore_assets(reader,request,source_payload)
    credential = os.environ.pop('NATIVE_OWNER_DATABASE_URL','')
    require(type(credential) is str and 0 < len(credential) <= 8192, 'LAUNCHER_CREDENTIAL')
    connection_parameters(credential,'neondb_owner')
    token = os.environ['GH_TOKEN']
    source_guard()
    PHASE = 'authenticated_stage'
    started = time.monotonic()
    run.assert_running()
    if request is not None:
        from ops.native_maintenance_runtime import DerivedStagePacket, run_identity
        from ops.native_maintenance_stage_unit import Retainer, UnitServer, read_accepted
        packet = DerivedStagePacket(request,run,manifest)
        verify_prior(reader,packet)
        scope = packet.scope_digest
        expected = dict(source=source,scope_digest=scope,stage=packet.stage,run=run_identity(run))
    else:
        from ops.native_maintenance_checkpoint_host_probe import identity
        scope = identity(source,run_id,attempt)[2]
    envelope = dict(driver=rpc.pack(wheels),credential=credential,token=token,request=rpc.pack(raw),job_id=run.job_id)
    if manifest is not None: envelope['manifest'] = rpc.pack(manifest)
    command = ssh_command(key,known_hosts,bootstrap(*args,mode))
    def prelaunch_guard():
        run.assert_running()
        if request is not None: request.assert_current()
    PHASE = 'request_retention'
    prelaunch = MeasuredStore(client,namespace,prelaunch_guard)
    retain_request(prelaunch,raw,accepted)
    # Complete command construction before launch; never launch before final auth.
    run.assert_running()
    PHASE = 'host_exchange'
    process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:
        channel = rpc.Channel(process.stdout.fileno(),process.stdin.fileno(),binding)
        def guard():
            channel.alive()
            run.assert_running()
            if request is not None: request.assert_current()
            channel.alive()
        store = MeasuredStore(client,namespace,guard)
        server = rpc.StoreServer(channel,scope,store,channel.alive)
        units = UnitServer(channel,Retainer(store,expected)) if packet is not None else None
        channel.send(frame(source_payload,envelope))
        while True:
            with measured('rpc_wait'):
                record = channel.receive()
            if record.get('kind') == 'NATIVE_REHEARSAL_DRAIN_DIAGNOSTIC':
                from ops.native_maintenance_coordination import validate_diagnostic_groups
                require(packet is None and set(record)=={'kind','binding','request_digest','groups','no_admission_authority'}
                    and record['binding']==binding and record['request_digest']==accepted
                    and record['no_admission_authority'] is True,'LAUNCHER_DIAGNOSTIC_SCHEMA')
                validate_diagnostic_groups(record['groups'])
                print(json.dumps(dict(audit='NATIVE_DRAIN_DIAGNOSTIC',groups=record['groups'],
                    no_admission_authority=True),sort_keys=True),flush=True)
                continue
            if record.get('kind') == 'NATIVE_REHEARSAL_REFUSED':
                from ops.native_maintenance_stage_rehearsal import PHASES, SAFE_CODES
                require(packet is None and set(record) == {
                    'kind','binding','request_digest','phase','code'}
                    and record['binding'] == binding and record['request_digest'] == accepted
                    and type(record['phase']) is str and record['phase'] in PHASES
                    and type(record['code']) is str and record['code'] in SAFE_CODES,
                    'LAUNCHER_REFUSAL_SCHEMA')
                PHASE = 'host_' + record['phase'] + ':' + record['code']
                raise RuntimeError('LAUNCHER_HOST_REFUSED')
            if record.get('kind') in ('NATIVE_STAGE_COMPLETE','NATIVE_REHEARSAL_COMPLETE'):
                break
            require(server.sequence < 128, 'LAUNCHER_REQUEST_LIMIT')
            if record.get('kind') == 'NATIVE_STAGE_UNIT_RETAIN':
                require(units is not None, 'LAUNCHER_REHEARSAL_UNIT_REFUSED')
                with measured('rpc_unit'):
                    units.accept(record)
            else:
                with measured('rpc_store'):
                    server.accept(record)
        require(record.get('binding') == binding and record.get('request_digest') == accepted
                and type(record.get('sequence')) is int and record['sequence'] == server.sequence,
                'LAUNCHER_COMPLETION_BINDING')
        if packet is not None:
            require(set(record) == {'kind','binding','request_digest','result','sequence'}
                    and record['kind'] == 'NATIVE_STAGE_COMPLETE' and units.retainer.used,
                    'LAUNCHER_STAGE_COMPLETION')
            result = record['result']
            require(type(result) is dict and set(result) == {
                'stage','scope_digest','head_digest','outcome','unit_digest','host_exited'}
                and result['stage'] == packet.stage and result['scope_digest'] == scope
                and bundle.identifier(result['head_digest'],64) and bundle.identifier(result['unit_digest'],64)
                and result['host_exited'] is False and result['outcome'] in ('UNKNOWN','BEFORE','AFTER'),
                'LAUNCHER_STAGE_RESULT')
            head = result['head_digest']
        else:
            require(set(record) == {'kind','binding','request_digest','scope_digest','head_digest','sequence',
                'elapsed_ms','snapshot_digest','timing_is_estimate','production_mutations','snapshot_approved','supervisor'}
                and record['kind'] == 'NATIVE_REHEARSAL_COMPLETE' and record['scope_digest'] == scope
                and bundle.identifier(record['head_digest'],64) and bundle.identifier(record['snapshot_digest'],64)
                and type(record['elapsed_ms']) is int and 0 <= record['elapsed_ms'] < 60000
                and record['timing_is_estimate'] is True and record['production_mutations'] is False
                and record['snapshot_approved'] is False, 'LAUNCHER_REHEARSAL_RESULT')
            head = record['head_digest']
        run.assert_running()
        channel.alive()
        process.stdin.close()
        require(process.wait(timeout=15) == 0, 'LAUNCHER_HOST_EXIT')
    finally:
        if process.returncode is None: stop_group(process)
        if not process.stdin.closed: process.stdin.close()
        process.stdout.close()
    PHASE = 'independent_readback_after_exit'
    source_guard()
    # New read-only adapter: does not depend on the now-closed host pipe, cannot PUT.
    final = MeasuredStore(client,namespace,read_only)
    checkpoint.accepted_latest(final,scope,head)
    if packet is not None:
        unit = json.loads(read_accepted(final,expected,result['unit_digest']),object_pairs_hook=unique)
        supervisor = unit['supervisor']
    else:
        supervisor = record['supervisor']
    verify_host_exit(ssh_command(key,known_hosts,bootstrap(*args,'drain')),source_payload,supervisor)
    source_guard()
    run.assert_running()
    report = dict(audit='NATIVE_STAGE_PASS' if packet is not None else 'NATIVE_REHEARSAL_PASS',
        source_sha=source,request_digest=accepted,scope_digest=scope,head_digest=head,
        run_id=run_id,attempt=attempt,job_id=run.job_id,host_exited=True,
        independent_readback=True,prior_host_drained=True,elapsed_ms=int((time.monotonic()-started)*1000))
    if packet is not None: report.update(stage=packet.stage,outcome=result['outcome'],unit_digest=result['unit_digest'])
    else: report.update(timing_is_estimate=True,production_mutations=False,snapshot_approved=False)
    print(json.dumps({**report,**timing()},sort_keys=True))


if __name__ == '__main__':
    try:
        require(len(sys.argv) > 1 and sys.argv[1] in ('stage','rehearsal'), 'LAUNCHER_ARGS')
        main(sys.argv[1])
    except BaseException as exc:
        print(json.dumps(dict(audit='NATIVE_LAUNCHER_REFUSED',phase=PHASE,code=failure_code(exc),**timing())))
        raise SystemExit(2) from None
