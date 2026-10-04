"""Pinned SSH delivery of independently accepted first-lane owner phases."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from ops import light_native_lane_controller as controller
from ops import light_native_pilot_release as release
from ops import native_maintenance_driver as driver
from ops.native_maintenance_store_runner import HOST,source_check
from ops.light_native_lane_run_guard import local_context


# Only fixed codes cross the SSH boundary; never print exception text or stderr.
FAILURE_REASONS=('LANE_ISSUER_NOT_ACCEPTED','LANE_ISSUER_SCOPE','LANE_ISSUER_AUTHORITY',
                 'LANE_RETIREMENT_UNKNOWN','LANE_RETIREMENT_REFUSED','LANE_RETIREMENT_REFERENCE',
                 'LANE_RETIREMENT_CANCELED_POLICY','LANE_RETIREMENT_CANCELED_WORK',
                 'LANE_RETIREMENT_PARTIAL_OR_CONFLICT','LANE_RETIREMENT_DRIFT',
                 'LANE_RETIREMENT_DATABASE_DRIFT','LANE_RETIREMENT_PINS','LANE_RETIREMENT_DESCENDANTS',
                 'LANE_ISSUER_EXPIRED','LANE_ISSUER_DUPLICATE','LANE_ISSUER_HISTORY',
                 'LANE_ISSUER_RECONCILIATION_REQUIRED','LANE_ISSUER_QUEUE_OR_DUPLICATE',
                 'LANE_ISSUER_CONTROLS','UNCLASSIFIED','LANE_OWNER_TERMINAL_POLICY_MISSING',
                 'LANE_OWNER_TERMINAL_POLICY_CHANGED','LANE_OWNER_OUTCOME_UNKNOWN',
                 'RPC_PRIVILEGE_MISMATCH','HELPER_ACCESS','NATIVE_TABLE_ACCESS',
                 'SCHEMA_USAGE_REQUIRED','PILOT_PUBLICATION_MARK_FENCED',
                 'LANE_RECOVERY_SCOPE','LANE_RECOVERY_CONTROLS_CHANGED',
                 'LANE_RECOVERY_WORK_CHANGED','LANE_RECOVERY_TASK_CHANGED',
                 'LANE_RECOVERY_DISPATCH_CHANGED','LANE_RECOVERY_UNEXPECTED_DELTA',
                 'LANE_RECOVERY_READBACK','LANE_RECOVERY_ROWCOUNT',
                 'LANE_RECOVERY_OUTCOME_UNKNOWN','LANE_RECOVERY_CLAIM_NOT_EXPIRED',
                 'PILOT_CLAIM_REFRESH_EXPIRED','PILOT_CLAIM_REFRESH_WINDOW',
                 'PILOT_CLAIM_REFRESH_DRIFT','PILOT_CLAIM_REFRESH_UNKNOWN',
                 'PILOT_CLAIM_REFRESH_DELTA','PILOT_CLAIM_REFRESH_READBACK',
                 'LANE_OWNER_PUBLICATION_UNCERTAIN','LANE_OWNER_PERMIT_UNCERTAIN',
                 'LANE_CYCLE_SCOPE','LANE_CYCLE_WINDOW','LANE_CYCLE_REPLAY',
                 'LANE_CYCLE_CHILD_UNKNOWN','LANE_CYCLE_CHILD_REFUSED',
                 'LANE_CYCLE_LOCK','LANE_CYCLE_RESULT','LANE_CYCLE_RECONCILIATION_REQUIRED')

def failure(exc):
    reason=str(exc)
    return dict(audit='LIGHT_LANE_OWNER_REFUSED',
                reason=reason if reason in FAILURE_REASONS else 'UNCLASSIFIED')


def remote_failure(raw):
    try:
        value=json.loads(raw)
    except (ValueError,UnicodeDecodeError):
        return failure(RuntimeError('LANE_OWNER_OUTCOME_UNKNOWN'))
    if (type(value) is dict and set(value)=={'audit','reason'}
            and value['audit']=='LIGHT_LANE_OWNER_REFUSED'
            and value['reason'] in FAILURE_REASONS):
        return value
    return failure(RuntimeError('LANE_OWNER_OUTCOME_UNKNOWN'))


def dispatch_inputs(event_path,source,*,read_only=False):
    """Validate before use; no input interpolation into runner logging surfaces."""
    from ops.light_native_retirement_live import public_reference
    from ops import light_native_retirement as r
    with open(event_path,'rb') as stream:
        event=r.parse(stream.read(1024*1024+1),limit=1024*1024)
    value=event['inputs']
    release.require(type(value) is dict and set(value)=={'expected_main_sha',
        'accepted_controller_sha256','accepted_runtime_sha256','accepted_payload_sha256',
        'retirement_reference_json'} and value['expected_main_sha']==source,'LANE_OWNER_NOT_ACCEPTED')
    digests=[value[k] for k in ('accepted_controller_sha256','accepted_runtime_sha256','accepted_payload_sha256')]
    release.require(all(r.digest(x) for x in digests),'LANE_OWNER_NOT_ACCEPTED')
    release.require(type(value['retirement_reference_json']) is str,'LANE_OWNER_NOT_ACCEPTED')
    payload=value['retirement_reference_json'].encode('utf-8')
    release.require(type(read_only) is bool,'LANE_OWNER_NOT_ACCEPTED')
    reference=public_reference(payload,action='observe-retirement-reference' if read_only else 'retire-prepare-reference')
    release.require(r.sha(payload)==digests[2] and reference['source']==source
        and reference['accepted_controller_sha256']==digests[0]
        and reference['accepted_runtime_sha256']==digests[1],'LANE_OWNER_NOT_ACCEPTED')
    return payload,digests


def bootstrap(source,accepted_controller,accepted_runtime,accepted_payload,wheel_sha,run,attempt,*,read_only=False):
    release.require(type(read_only) is bool,'LANE_OWNER_RUN')
    for digest in (accepted_controller,accepted_runtime,accepted_payload,wheel_sha):
        release.require(release.source.identifier(digest,64),'LANE_OWNER_DIGEST')
    release.require(release.source.identifier(source,40) and type(run) is int and run>0
                    and type(attempt) is int and attempt>0,'LANE_OWNER_RUN')
    return 'FAILURE_REASONS='+repr(FAILURE_REASONS)+'\n'+'''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 wire=sys.stdin.buffer.read(24*1024*1024+1)
 assert len(wire)<=24*1024*1024 and os.geteuid()==0
 value=json.loads(wire)
 assert set(value)=={'controller','runtime','payload','driver','credential','token'}
 decoded={key:base64.b64decode(value[key],validate=True) for key in ('controller','runtime','payload','driver')}
 for key,digest in %r.items():assert hashlib.sha256(decoded[key]).hexdigest()==digest
 package=json.loads(decoded['controller'])
 assert package['source']==%r and package['kind']=='LIGHT_LANE_CONTROLLER' and package['version']==1
 assert set(package['helpers'])==set(%r)
 with tempfile.TemporaryDirectory(prefix='light-lane-owner-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp)
  for directory in ('ops','database'):(root/directory).mkdir(mode=0o700)
  for name,text in package['helpers'].items():
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream:stream.write(text)
  sys.path.insert(0,str(root))
  sys.path.append('/opt/bridge-school/school-autopilot-production-light/releases/f82d58efabf21ba62fd242a4fa02a8e7cfda1d23')
  from ops.light_native_lane_run_guard import authenticated
  from ops.light_native_lane_controller import phase
  from ops.light_native_retirement_live import public_reference,public_result,public_observation_result,inspect_retirement
  reference=public_reference(decoded['payload'],action=%r)
  if %r:
   guard=authenticated(%r,%d,%d,value['token'],read_only=True)
   result=inspect_retirement(decoded['driver'],value['credential'],decoded['controller'],decoded['runtime'],decoded['payload'],%r,guard)
   check_result=public_observation_result
  else:
   guard=authenticated(%r,%d,%d,value['token'])
   result=phase(decoded['driver'],value['credential'],value['token'],decoded['controller'],decoded['runtime'],decoded['payload'],%r,guard)
   check_result=public_result
  guard.assert_running()
  print(json.dumps(check_result(result,reference['record_sha256']),sort_keys=True))
except BaseException as exc:
 reason=str(exc)
 print(json.dumps(dict(audit='LIGHT_LANE_OWNER_REFUSED',reason=reason if reason in FAILURE_REASONS else 'UNCLASSIFIED'),sort_keys=True))
 raise SystemExit(2) from None
''' % (dict(controller=accepted_controller,runtime=accepted_runtime,payload=accepted_payload,driver=wheel_sha),
       source,tuple(dict.fromkeys((*release.HELPERS,*controller.EXTRA))),
       'observe-retirement-reference' if read_only else 'retire-prepare-reference',read_only,
       source,run,attempt,accepted_payload,source,run,attempt,accepted_payload)


def main(*,read_only=False):
    release.require(len(sys.argv)==4,'LANE_OWNER_ARGUMENTS')
    key,known,wheel_directory=sys.argv[1:]
    source,run,attempt=local_context(os.environ,read_only=read_only)
    payload,digests=dispatch_inputs(os.environ['GITHUB_EVENT_PATH'],source,read_only=read_only)
    source_check(source)
    repo=Path(__file__).resolve().parents[1]
    raw=controller.package(repo,source)
    retained=release.package(repo,controller.install.RETAINED_SOURCE)
    from ops.light_native_retirement_live import public_result,public_reference,public_observation_result
    wheels=driver.build(wheel_directory)
    release.require([controller.sha(item) for item in (raw,retained,payload)]==digests,'LANE_OWNER_NOT_ACCEPTED')
    code=bootstrap(source,*digests,driver.sha(wheels),run,attempt,read_only=read_only)
    wire=controller.encoded(dict(controller=base64.b64encode(raw).decode(),runtime=base64.b64encode(retained).decode(),
        payload=base64.b64encode(payload).decode(),driver=base64.b64encode(wheels).decode(),
        credential=os.environ.pop('NATIVE_OWNER_DATABASE_URL',''),token=os.environ['GH_TOKEN']))
    command=['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1','-o','ServerAliveInterval=10',
        '-o','ServerAliveCountMax=2',HOST,shlex.join(['sudo','-n','/usr/bin/python3','-I','-S','-B','-c',code])]
    source_check(source)
    result=subprocess.run(command,input=wire,capture_output=True,timeout=1950,env={'PATH':'/usr/bin:/bin'})
    release.require(len(result.stdout)<=262144,'LANE_OWNER_OUTCOME_UNKNOWN')
    if result.returncode!=0:
        print(json.dumps(remote_failure(result.stdout),sort_keys=True))
        return 2
    reference=public_reference(payload,action='observe-retirement-reference' if read_only else 'retire-prepare-reference')
    value=(public_observation_result if read_only else public_result)(json.loads(result.stdout),reference['record_sha256'])
    source_check(source)
    print(json.dumps(value,sort_keys=True))
    return 2 if read_only and value['state']!='OBSERVED' else 0


if __name__=='__main__':
    try:status=main()
    except BaseException:
        print('{"audit":"LIGHT_LANE_RUNNER_REFUSED"}')
        raise SystemExit(2) from None
    else:
        raise SystemExit(status or 0)
