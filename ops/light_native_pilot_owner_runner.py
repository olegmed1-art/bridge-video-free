"""Supervised SSH transport for fixed, independently accepted pilot owner steps."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from ops import light_native_pilot_release as release
from ops import native_maintenance_driver as driver
from ops.native_maintenance_store_runner import HOST, loader, source_check
from ops.light_native_pilot_run_guard import local_context
from ops.oracle_autopilot_source_preflight import connection_parameters

MAX_WIRE=20*1024*1024
require=release.require


def bootstrap(package, source, accepted_package, accepted_payload, wheel_sha, run_id, attempt):
    require(release.source.identifier(source,40)
            and release.source.identifier(accepted_payload,64)
            and release.source.identifier(wheel_sha,64)
            and release.source.identifier(accepted_package,64)
            and release.hashlib.sha256(package).hexdigest()==accepted_package,
            'PILOT_OWNER_BOOTSTRAP_BINDING')
    obj=json.loads(package)
    lifetime=obj['helpers']['ops/native_maintenance_lifetime.py'].encode()
    inner='''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 wire=sys.stdin.buffer.read(%d)
 assert len(wire)<=%d
 value=json.loads(wire)
 assert set(value)=={'package','payload','driver','credential','token'}
 raw=base64.b64decode(value['package'],validate=True)
 assert hashlib.sha256(raw).hexdigest()==%r
 package=json.loads(raw)
 assert package['source']==%r and package['version']==1
 assert set(package['helpers'])==set(%r)
 wheels=base64.b64decode(value['driver'],validate=True)
 assert hashlib.sha256(wheels).hexdigest()==%r
 payload=base64.b64decode(value['payload'],validate=True)
 assert hashlib.sha256(payload).hexdigest()==%r
 with tempfile.TemporaryDirectory(prefix='light-pilot-owner-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp)
  (root/'ops').mkdir(mode=0o700)
  for name,text in package['helpers'].items():
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream:stream.write(text)
  sys.path.insert(0,str(root))
  from ops import light_native_pilot_release as release
  candidate=pathlib.Path('/opt/bridge-school/school-autopilot-production-light/releases')/%r
  release.validate(package['runtime'],%r,package['runtime']['sha256'])
  release.staging.verify_release(candidate,package['runtime'])
  sys.path.append(str(candidate))
  from ops.light_native_pilot_run_guard import authenticated
  from ops.light_native_pilot_owner import step
  run_guard=authenticated(%r,%d,%d,value['token'])
  result=step(wheels,value['credential'],value['token'],raw,payload,%r,run_guard)
  run_guard.assert_running()
  print(json.dumps(result,sort_keys=True))
except BaseException:
 print('{"audit":"LIGHT_NATIVE_OWNER_STEP_REFUSED"}')
 raise SystemExit(2) from None
''' % (MAX_WIRE+1,MAX_WIRE,accepted_package,source,release.HELPERS,wheel_sha,
       accepted_payload,source,source,source,run_id,attempt,accepted_payload)
    # Reuse the already rehearsed PID1 lifetime primitive; authority comes
    # exclusively from the fixed owner step, accepted package/plan/window.
    outer=('import base64,types\n'+loader('lifetime',lifetime)+
        'try:\n result=lifetime.managed('+repr(inner)+','+
        repr(base64.b64encode(lifetime).decode())+','+repr(source)+','+repr(str(run_id)+'-'+str(attempt))+')\n'+
        'except BaseException:\n result=2\nraise SystemExit(result)\n')
    require(len(outer.encode())<98304,'PILOT_OWNER_BOOTSTRAP_SIZE')
    return outer


def main():
    require(len(sys.argv)==4,
            'PILOT_OWNER_RUNNER_CONTEXT')
    key,known_hosts,wheel_directory=sys.argv[1:]
    source,run_id,attempt=local_context(os.environ)
    require(source==os.environ['EXPECTED_MAIN'],'PILOT_OWNER_SOURCE_CHANGED')
    source_check(source)
    repo=Path(__file__).resolve().parents[1]
    package=release.package(repo,source)
    accepted_package=os.environ['PILOT_ACCEPTED_PACKAGE']
    require(release.hashlib.sha256(package).hexdigest()==accepted_package,'PILOT_OWNER_PACKAGE')
    payload=base64.b64decode(os.environ['PILOT_PAYLOAD_BASE64'],validate=True)
    accepted_payload=os.environ['PILOT_ACCEPTED_PAYLOAD']
    require(len(payload)<=262144 and release.hashlib.sha256(payload).hexdigest()==accepted_payload,
            'PILOT_OWNER_PAYLOAD')
    wheels=driver.build(wheel_directory)
    credential=os.environ.pop('NATIVE_OWNER_DATABASE_URL','')
    connection_parameters(credential,'neondb_owner')
    wire=release.encoded(dict(package=base64.b64encode(package).decode(),
        payload=base64.b64encode(payload).decode(),driver=base64.b64encode(wheels).decode(),
        credential=credential,token=os.environ['GH_TOKEN']))
    require(len(wire)<=MAX_WIRE,'PILOT_OWNER_WIRE_SIZE')
    code=bootstrap(package,source,accepted_package,accepted_payload,driver.sha(wheels),run_id,attempt)
    command=['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known_hosts,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1',HOST,
        shlex.join(['sudo','-n','/usr/bin/python3','-I','-S','-B','-c',code])]
    source_check(source)
    result=subprocess.run(command,input=wire,capture_output=True,timeout=115,env={'PATH':'/usr/bin:/bin'})
    require(result.returncode==0 and len(result.stdout)<=262144,'PILOT_OWNER_OUTCOME_UNKNOWN')
    value=json.loads(result.stdout)
    require(type(value) is dict,'PILOT_OWNER_RESPONSE')
    source_check(source)
    print(json.dumps(value,sort_keys=True))


if __name__=='__main__':
    try:main()
    except BaseException:
        print('{"audit":"LIGHT_NATIVE_OWNER_RUNNER_REFUSED"}')
        raise SystemExit(2) from None
