"""Authenticated single dispatch of the fixed incident recovery controller."""
import ast
import base64
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_checkpoint as cp
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_checkpoint_transport as rpc
from ops import native_maintenance_driver as driver
from ops.native_maintenance_run_guard import PersistentAPI
from ops.native_maintenance_store_runner import loader, HOST
from ops.native_maintenance_stage_launcher import oci_client, restore_assets, REQUEST_PREFIX
from ops.native_maintenance_stage_request import AcceptedRequest
from ops.native_maintenance_stage_unit import read_accepted
from ops.native_maintenance_workflow_pause import digest, encoded
from ops.native_maintenance_readonly_transport import stop_group

ROOT=Path(__file__).resolve().parents[2]
NAMES={'auth':'light_restore_authority_20260928.py','probe':'light_after_probe_20260928.py',
       'life':'light_restore_lifetime_20260928.py','core':'light_restore_core_20260928.py','host':'light_restore_host_20260928.py'}
def load(name):
    spec=importlib.util.spec_from_file_location('incident_'+name,ROOT/'ops/incident'/NAMES[name])
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

auth,probe,life=load('auth'),load('probe'),load('life')
PHASE='context'
EFFECTS_POSSIBLE=False


def context(env):
    execution=env.get('ACCEPTED_EXECUTION_SHA','')
    raw=env.get('INCIDENT_AUTHORIZATION','').encode()
    auth.authorization(raw,execution)
    auth.require(env.get('GITHUB_REPOSITORY')==auth.REPO
        and env.get('GITHUB_REF')=='refs/heads/'+auth.BRANCH
        and env.get('GITHUB_SHA')==env.get('GITHUB_WORKFLOW_SHA')==execution
        and env.get('GITHUB_WORKFLOW_REF')==auth.REPO+'/'+auth.WORKFLOW+'@refs/heads/'+auth.BRANCH
        and env.get('GITHUB_EVENT_NAME')=='workflow_dispatch'
        and env.get('GITHUB_ACTOR')==env.get('GITHUB_TRIGGERING_ACTOR')==auth.OWNER
        and env.get('GITHUB_JOB')=='restore'
        and re.fullmatch('[1-9][0-9]{0,19}',env.get('GITHUB_RUN_ID',''))
        and env.get('GITHUB_RUN_ATTEMPT')=='1','INCIDENT_CONTEXT')
    return execution,raw,int(env['GITHUB_RUN_ID']),1


def safe_codes(files):
    codes={'UNCLASSIFIED','TIMEOUT','DB_AUTHENTICATION'}
    for raw in files.values():
        for node in ast.walk(ast.parse(raw)):
            if isinstance(node,ast.Constant) and type(node.value) is str and re.fullmatch('[A-Z][A-Z0-9_]{2,79}',node.value):
                codes.add(node.value)
    return codes


def bootstrap(files,execution,run_id,attempt,workflow_digest,binding):
    lifetime=files['life']
    codes=safe_codes({**files,'runner':Path(__file__).read_bytes()})
    code='import base64,types,sys,os,json,signal\n'+''.join(loader(k,v) for k,v in files.items())
    code += "def expired(*_): raise TimeoutError()\nsignal.signal(signal.SIGALRM,expired);signal.alarm(540)\n"
    code += "def exact(n):\n raw=bytearray()\n while len(raw)<n:\n  part=os.read(0,min(65536,n-len(raw)))\n  auth.require(part,'INCIDENT_FRAME_EOF')\n  raw.extend(part)\n return bytes(raw)\n"
    code += "try:\n auth.require(sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode and os.getuid()==0 and os.uname().nodename=='autopilot-lite-vnic','INCIDENT_HOST')\n"
    code += " size=int.from_bytes(exact(4),'big');auth.require(0<size<=23*1024*1024,'INCIDENT_FRAME_SIZE')\n raw=exact(size);value=json.loads(raw,object_pairs_hook=auth.unique);auth.require(auth.encoded(value)==raw,'INCIDENT_FRAME_CANONICAL')\n"
    code += ' host.execute(value,'+','.join(map(repr,(execution,run_id,attempt,workflow_digest,binding)))+',auth,probe,core,life)\n'
    code += "except BaseException as exc:\n value=exc.args[0] if len(exc.args)==1 and type(exc.args[0]) is str else None\n"
    code += ' code=value if value in '+repr(sorted(codes))+" else ('TIMEOUT' if isinstance(exc,TimeoutError) else 'UNCLASSIFIED')\n"
    code += " phase=exc.phase if isinstance(exc,core.RestoreRefused) else host.PHASE\n"
    code += " report=dict(kind='INCIDENT_REFUSED',binding="+repr(binding)+",phase=phase,code=code,effects_possible=host.EFFECTS_POSSIBLE)\n"
    code += " wire=auth.encoded(report);os.write(1,len(wire).to_bytes(4,'big')+wire);raise SystemExit(2) from None\nfinally:\n signal.alarm(0)\n"
    outer='import base64,types\n'+loader('lifetime',lifetime)
    outer+='try:\n result=lifetime.managed('+repr(code)+','+repr(base64.b64encode(lifetime).decode())+','+repr(execution)+','+repr(str(run_id)+'-'+str(attempt))+')\nexcept BaseException:\n result=2\nraise SystemExit(result)\n'
    auth.require(len(outer.encode())<=110000,'INCIDENT_BOOTSTRAP_SIZE')
    return outer,codes


def ssh(key,known,code):
    return ['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1','-o','ServerAliveInterval=5',
        '-o','ServerAliveCountMax=2',HOST,shlex.join(['sudo','-n','/usr/bin/python3','-I','-B','-S','-c',code])]


def receipt(raw,authority):
    value=json.loads(raw,object_pairs_hook=auth.unique)
    auth.require(type(value) is dict and set(value)=={'version','kind','authorization','authorization_digest',
        'execution','historical_source','scope','historical_request','run','supervisor'}
        and encoded(value)==raw and type(value['version']) is int and value['version']==1
        and value['kind']=='LIGHT_INCIDENT_RESTORE_RECEIPT'
        and value['authorization']==authority.value and value['authorization_digest']==authority.accepted
        and value['execution']==authority.execution and value['historical_source']==auth.SOURCE
        and value['scope']==auth.SCOPE and value['historical_request']==probe.REQUEST
        and value['run']==authority.run_identity,'INCIDENT_RECEIPT')
    unit=value['supervisor']
    prefix='bridge-native-ro-'+authority.execution[:12]+'-'+str(authority.run_id)+'-'+str(authority.attempt)+'-'
    auth.require(type(unit) is dict and set(unit)=={'unit','invocation','cgroup_inode'}
        and re.fullmatch(re.escape(prefix)+'[0-9a-f]{16}\\.service',unit['unit'])
        and re.fullmatch('[0-9a-f]{32}',unit['invocation'])
        and type(unit['cgroup_inode']) is int and unit['cgroup_inode']>0,'INCIDENT_RECEIPT_UNIT')
    return value


def retain_once(store,key,raw):
    store.assert_private()
    auth.require(store._read(key,32768) is None,'INCIDENT_REMOTE_CLAIM_EXISTS')
    store._budget(len(raw));store._put(key,raw)
    read=store._read(key,32768)
    auth.require(read is not None and read[0]==raw,'INCIDENT_REMOTE_RECEIPT_ACK')
    store.guard()


def main():
    global PHASE,EFFECTS_POSSIBLE
    auth.require(len(sys.argv)==4,'INCIDENT_ARGS')
    execution,authorization,run_id,attempt=context(os.environ)
    key,known,wheels_dir=sys.argv[1:]
    token=os.environ.pop('GH_TOKEN','');credential=os.environ.pop('NATIVE_OWNER_DATABASE_URL','')
    auth.require(0<len(token)<=4096 and 0<len(credential)<=8192,'INCIDENT_CREDENTIAL')
    files={name:bundle.git(ROOT,'show',execution+':ops/incident/'+path) for name,path in NAMES.items()}
    auth.require(all((ROOT/'ops/incident'/NAMES[n]).read_bytes()==raw for n,raw in files.items()),'INCIDENT_CHECKOUT')
    auth.require((ROOT/'ops/incident/light_restore_runner_20260928.py').read_bytes()==bundle.git(ROOT,'show',execution+':ops/incident/light_restore_runner_20260928.py'),'INCIDENT_CHECKOUT')
    workflow=bundle.git(ROOT,'show',execution+':'+auth.WORKFLOW)
    payload=bundle.build(ROOT,auth.SOURCE);auth.require(bundle.digest(payload)==probe.BUNDLE,'INCIDENT_SOURCE')
    wheels=driver.build(wheels_dir)
    with PersistentAPI(token) as api:
        authority=auth.Authority(authorization,execution,run_id,attempt,api,auth.sha(workflow),host=False)
        PHASE='preflight';authority.assert_live()
        client,namespace=oci_client()
        store=adapter.OCIJournalStore(client,namespace,authority.assert_live)
        store.assert_private()
        auth.require(store._read(auth.PREFIX+'claim.json',32768) is None,'INCIDENT_REMOTE_CLAIM_EXISTS')
        retained=store._read(REQUEST_PREFIX+probe.REQUEST+'.json',262144)
        auth.require(retained is not None and auth.sha(retained[0])==probe.REQUEST,'INCIDENT_ORIGINAL_REQUEST')
        request=AcceptedRequest(retained[0],probe.REQUEST,auth.SOURCE)
        manifest=restore_assets(store,request,payload)
        for row in request.value['packet']['prior_units']:
            expected={k:row[k] for k in ('source','scope_digest','stage','run')}
            auth.require(read_accepted(store,expected,digest(row))==encoded(row),'INCIDENT_PRIORS')
        cp.accepted_latest(store,auth.SCOPE,auth.HEAD)
        binding=auth.sha(encoded(dict(execution=execution,authorization=authority.accepted,run=authority.run_identity,
                                     helpers={k:auth.sha(v) for k,v in files.items()})))
        code,codes=bootstrap(files,execution,run_id,attempt,auth.sha(workflow),binding)
        value=dict(source=base64.b64encode(payload).decode(),driver=base64.b64encode(wheels).decode(),
            request=base64.b64encode(retained[0]).decode(),manifest=base64.b64encode(manifest).decode(),
            authorization=authorization.decode(),token=token,credential=credential)
        authority.assert_live()
        auth.require(authority.deadline-__import__('time').monotonic()>760,'INCIDENT_PRELAUNCH_BUDGET')
        PHASE='host';EFFECTS_POSSIBLE=True
        process=subprocess.Popen(ssh(key,known,code),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin'},start_new_session=True)
        done=None;accepted_receipt=None
        try:
            channel=life.channel(process.stdout.fileno(),process.stdin.fileno(),binding)
            channel.send(value)
            ready=channel.receive()
            if ready.get('kind')=='INCIDENT_REFUSED':
                auth.require(set(ready)=={'kind','binding','phase','code','effects_possible'}
                    and ready['binding']==binding and ready['code'] in codes
                    and type(ready['phase']) is str and re.fullmatch('[a-z_]{1,50}',ready['phase'])
                    and type(ready['effects_possible']) is bool,'INCIDENT_REFUSAL_SCHEMA')
                print(json.dumps(ready,sort_keys=True));raise RuntimeError('INCIDENT_HOST_REFUSED')
            auth.require(ready==dict(kind='INCIDENT_READY',binding=binding,authorization_digest=authority.accepted),'INCIDENT_READY')
            authority.assert_live()
            auth.require(min(channel.deadline,authority.deadline-60)-__import__('time').monotonic()>630,'INCIDENT_STARTUP_BUDGET')
            channel.send(dict(kind='INCIDENT_START',binding=binding,authorization_digest=authority.accepted))
            server=rpc.StoreServer(channel,auth.SCOPE,store,authority.assert_current)
            while True:
                message=channel.receive()
                kind=message.get('kind')
                if kind=='INCIDENT_REFUSED':
                    auth.require(set(message)=={'kind','binding','phase','code','effects_possible'}
                        and message['binding']==binding and message['code'] in codes
                        and type(message['phase']) is str and re.fullmatch('[a-z_]{1,50}',message['phase'])
                        and type(message['effects_possible']) is bool,'INCIDENT_REFUSAL_SCHEMA')
                    print(json.dumps(message,sort_keys=True));raise RuntimeError('INCIDENT_HOST_REFUSED')
                if kind=='INCIDENT_RECEIPT':
                    auth.require(accepted_receipt is None and set(message)=={'kind','binding','data'}
                        and message['binding']==binding,'INCIDENT_RECEIPT_SEQUENCE')
                    raw=rpc.unpack(message['data'],32768);accepted_receipt=receipt(raw,authority)
                    authority.assert_live();retain_once(store,auth.PREFIX+'claim.json',raw)
                    channel.send(dict(kind='INCIDENT_RECEIPT_ACK',binding=binding,digest=auth.sha(raw)))
                elif kind=='INCIDENT_COMPLETE':
                    auth.require(accepted_receipt is not None and set(message)=={'kind','binding','authorization_digest','receipt_digest','result','supervisor'}
                        and message['binding']==binding and message['authorization_digest']==authority.accepted
                        and message['receipt_digest']==auth.sha(encoded(accepted_receipt))
                        and message['supervisor']==accepted_receipt['supervisor'],'INCIDENT_COMPLETE_SCHEMA')
                    result=message['result']
                    auth.require(type(result) is dict and set(result)=={'scope_digest','head_digest','pair_digest','outcome','restored_workflow_ids'}
                        and result['scope_digest']==auth.SCOPE and result['outcome']=='AFTER'
                        and result['restored_workflow_ids']==[343949665]
                        and all(re.fullmatch('[0-9a-f]{64}',result[k]) for k in ('head_digest','pair_digest')),
                        'INCIDENT_COMPLETE_RESULT')
                    done=message;break
                else:
                    auth.require(accepted_receipt is not None and server.sequence<256,'INCIDENT_RPC_BEFORE_RECEIPT')
                    server.accept(message)
            auth.require(process.wait(timeout=15)==0,'INCIDENT_HOST_EXIT')
        finally:
            if process.poll() is None: stop_group(process)
        PHASE='final_readback';authority.assert_live()
        raw=cp.accepted_latest(store,auth.SCOPE,done['result']['head_digest'])
        auth.require(auth.sha(raw)==done['result']['pair_digest'],'INCIDENT_FINAL_READBACK')
        # Private complete evidence is retained before printing success.
        retain_once(store,auth.PREFIX+'completed.json',encoded(done))
        print(json.dumps(dict(audit='LIGHT_INCIDENT_RESTORE',status='PASS',execution=execution,
            authorization_digest=authority.accepted,**done['result'],hold_released=False,
            permission_session_repeated=False,host_exited=True),sort_keys=True))
    return 0


if __name__=='__main__':
    try: result=main()
    except BaseException as exc:
        value=exc.args[0] if len(exc.args)==1 and type(exc.args[0]) is str else None
        files={k:(ROOT/'ops/incident'/v).read_bytes() for k,v in NAMES.items()}
        codes=safe_codes({**files,'runner':Path(__file__).read_bytes()})
        print(json.dumps(dict(audit='LIGHT_INCIDENT_RESTORE',status='REFUSED',phase=PHASE,
            code=value if value in codes else 'UNCLASSIFIED',effects_possible=EFFECTS_POSSIBLE),sort_keys=True))
        result=2
    sys.exit(result)
