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
    source_check(os.environ.get('EXPECTED_MAIN'))


def bootstrap(repo, source, source_digest, wheel_digest, run_id, attempt, accepted, binding, mode):
    require(mode in ('fetch', 'drain', 'first_install', 'stage', 'rehearsal') and bundle.identifier(accepted,64)
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


def main(mode):
    global PHASE
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
    require(action in ('rehearse','first_install') and (mode == 'rehearsal' or action == 'rehearse'),
            'LAUNCHER_PROVISION_MODE')
    if action == 'first_install':
        require(bundle.digest(first_install_intent(source)) == accepted, 'LAUNCHER_PROVISION_INTENT')
        PHASE = 'explicit_first_install'
        run = cls(source,run_id,attempt,API(os.environ['GH_TOKEN']))
        run.assert_running()
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
    else:
        from ops.native_maintenance_stage_rehearsal import request_value
        request_value(raw,accepted,source)
    PHASE = 'private_recovery_readback'
    client, namespace = oci_client()
    # Preparatory operations are read-only. No run lease is renewed after start.
    def read_only(): raise RuntimeError('LAUNCHER_READ_ONLY_STORE')
    reader = adapter.OCIJournalStore(client,namespace,read_only)
    if request is not None:
        manifest = restore_assets(reader,request,source_payload)
    credential = os.environ.pop('NATIVE_OWNER_DATABASE_URL','')
    require(type(credential) is str and 0 < len(credential) <= 8192, 'LAUNCHER_CREDENTIAL')
    connection_parameters(credential,'neondb_owner')
    token = os.environ['GH_TOKEN']
    source_guard()
    PHASE = 'authenticated_stage'
    started = time.monotonic()
    run = cls(source,run_id,attempt,API(token))
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
    prelaunch = adapter.OCIJournalStore(client,namespace,prelaunch_guard)
    retain_request(prelaunch,raw,accepted)
    # Complete command construction before launch; never launch before final auth.
    run.assert_running()
    process = subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
        env={'PATH':'/usr/bin:/bin'},start_new_session=True)
    try:
        channel = rpc.Channel(process.stdout.fileno(),process.stdin.fileno(),binding)
        def guard():
            channel.alive()
            run.assert_running()
            if request is not None: request.assert_current()
            channel.alive()
        store = adapter.OCIJournalStore(client,namespace,guard)
        server = rpc.StoreServer(channel,scope,store,channel.alive)
        units = UnitServer(channel,Retainer(store,expected)) if packet is not None else None
        channel.send(frame(source_payload,envelope))
        while True:
            record = channel.receive()
            if record.get('kind') in ('NATIVE_STAGE_COMPLETE','NATIVE_REHEARSAL_COMPLETE'):
                break
            require(server.sequence < 128, 'LAUNCHER_REQUEST_LIMIT')
            if record.get('kind') == 'NATIVE_STAGE_UNIT_RETAIN':
                require(units is not None, 'LAUNCHER_REHEARSAL_UNIT_REFUSED')
                units.accept(record)
            else: server.accept(record)
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
    final = adapter.OCIJournalStore(client,namespace,read_only)
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
    print(json.dumps(report,sort_keys=True))


if __name__ == '__main__':
    try:
        require(len(sys.argv) > 1 and sys.argv[1] in ('stage','rehearsal'), 'LAUNCHER_ARGS')
        main(sys.argv[1])
    except BaseException:
        print(json.dumps(dict(audit='NATIVE_LAUNCHER_REFUSED',phase=PHASE)))
        raise SystemExit(2) from None
