"""Fixed authenticated READ_ONLY inventory; no database credential or driver."""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


def bootstrap(source,controller_pin,runtime_pin,payload_pin,run,attempt):
    for value,size in ((source,40),(controller_pin,64),(runtime_pin,64),(payload_pin,64)):
        if type(value) is not str or re.fullmatch('[0-9a-f]{'+str(size)+'}',value) is None:
            raise ValueError('INVENTORY_BOOTSTRAP_BINDING')
    if not (type(run) is int and 1<=run<10**20
            and type(attempt) is int and 1<=attempt<10**6):
        raise ValueError('INVENTORY_BOOTSTRAP_BINDING')
    return '''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 assert os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic'
 wire=sys.stdin.buffer.read(12*1024*1024+1)
 assert len(wire)<=12*1024*1024
 envelope=json.loads(wire)
 assert type(envelope) is dict and set(envelope)=={'controller','runtime','payload','token'}
 decoded={key:base64.b64decode(envelope[key],validate=True) for key in ('controller','runtime','payload')}
 for key,pin in %r.items():assert hashlib.sha256(decoded[key]).hexdigest()==pin
 package=json.loads(decoded['controller'])
 assert package['source']==%r and package['kind']=='LIGHT_LANE_CONTROLLER' and package['version']==1
 with tempfile.TemporaryDirectory(prefix='light-native-inventory-',dir='/var/tmp') as temporary:
  root=pathlib.Path(temporary)
  for name in ('ops','database'):(root/name).mkdir(mode=0o700)
  for name,text in package['helpers'].items():
   assert type(name) is str and len(name)<200 and name.count('/')==1 and name.endswith('.py')
   assert name.split('/')[0] in ('ops','database') and all(part not in ('','..','.') for part in name.split('/'))
   assert type(text) is str
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream:stream.write(text)
  sys.path.insert(0,str(root))
  sys.path.append('/opt/bridge-school/school-autopilot-production-light/releases/f82d58efabf21ba62fd242a4fa02a8e7cfda1d23')
  from ops import light_native_retirement as r
  from ops.light_native_lane_controller import validate_package
  from ops.light_native_lane_run_guard import authenticated
  from ops.light_native_retirement_live import inventory_issuer_inputs
  validate_package(decoded['controller'],%r,%r)
  reference=r.issuer_inventory_reference(decoded['payload'])
  assert reference['source']==%r and reference['accepted_controller_sha256']==%r and reference['accepted_runtime_sha256']==%r
  guard=authenticated(%r,%d,%d,envelope['token'],read_only=True)
  result=inventory_issuer_inputs(decoded['controller'],decoded['runtime'],decoded['payload'],%r,guard)
  guard.assert_running()
  refused=result.get('audit')=='LIGHT_NATIVE_ISSUER_INPUT_REFUSED'
  checked=r.public_inventory_failure(result) if refused else r.public_issuer_inventory(result,reference['policy_sha256'],reference['index'])
  print(json.dumps(checked,sort_keys=True))
  status=2 if refused else 0
except BaseException:
 print('{"audit":"LIGHT_NATIVE_ISSUER_INPUT_REFUSED","reason":"OBSERVATION_REFUSED"}')
 status=2
raise SystemExit(status)
''' % (dict(controller=controller_pin,runtime=runtime_pin,payload=payload_pin),source,
       source,controller_pin,source,controller_pin,runtime_pin,source,run,attempt,payload_pin)


def dispatch_inputs(event_path,source):
    from ops import light_native_retirement as r
    event=r.parse(Path(event_path).read_bytes(),limit=1024*1024)
    inputs=event['inputs']
    r.need(type(inputs) is dict and set(inputs)=={'expected_main_sha',
        'accepted_controller_sha256','accepted_runtime_sha256','accepted_payload_sha256',
        'retirement_reference_json'} and inputs['expected_main_sha']==source,
        'LANE_ISSUER_INVENTORY_REFERENCE')
    raw=inputs['retirement_reference_json'].encode('utf-8')
    reference=r.issuer_inventory_reference(raw)
    pins=[inputs[key] for key in ('accepted_controller_sha256','accepted_runtime_sha256','accepted_payload_sha256')]
    r.need(reference['source']==source and reference['accepted_controller_sha256']==pins[0]
        and reference['accepted_runtime_sha256']==pins[1] and r.sha(raw)==pins[2],
        'LANE_ISSUER_INVENTORY_REFERENCE')
    return raw,pins,reference


def main():
    from ops import light_native_retirement as r
    from ops import light_native_lane_controller as owner
    from ops import light_native_pilot_release as release
    from ops.light_native_lane_run_guard import local_context
    from ops.native_maintenance_store_runner import HOST,source_check
    r.need(len(sys.argv)==3,'LANE_ISSUER_INVENTORY_REFERENCE')
    key,known=sys.argv[1:]
    source,run,attempt=local_context(os.environ,read_only=True)
    payload,pins,reference=dispatch_inputs(os.environ['GITHUB_EVENT_PATH'],source)
    source_check(source)
    repo=Path(__file__).resolve().parents[1]
    controller=owner.package(repo,source)
    runtime=release.package(repo,owner.install.RETAINED_SOURCE)
    r.need([r.sha(value) for value in (controller,runtime,payload)]==pins,
           'LANE_ISSUER_INVENTORY_BINDING')
    wire=r.encoded(dict(controller=base64.b64encode(controller).decode(),
        runtime=base64.b64encode(runtime).decode(),payload=base64.b64encode(payload).decode(),
        token=os.environ['GH_TOKEN']))
    code=bootstrap(source,*pins,run,attempt)
    command=['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1','-o','ServerAliveInterval=10',
        '-o','ServerAliveCountMax=2',HOST,shlex.join(['sudo','-n','/usr/bin/python3','-I','-S','-B','-c',code])]
    source_check(source)
    result=subprocess.run(command,input=wire,capture_output=True,timeout=150,env={'PATH':'/usr/bin:/bin'})
    r.need(len(result.stdout)<=4096,'LANE_ISSUER_INVENTORY_OUTPUT')
    value=r.parse(result.stdout,limit=4096)
    if result.returncode:
        print(json.dumps(r.public_inventory_failure(value),sort_keys=True))
        return 2
    checked=r.public_issuer_inventory(value,reference['policy_sha256'],reference['index'])
    source_check(source)
    print(json.dumps(checked,sort_keys=True))
    return 0


if __name__=='__main__':
    try:status=main()
    except BaseException:
        print('{"audit":"LIGHT_NATIVE_ISSUER_INPUT_REFUSED","reason":"OBSERVATION_REFUSED"}')
        raise SystemExit(2) from None
    raise SystemExit(status)
