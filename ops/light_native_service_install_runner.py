"""One exact qualified dormant installation through the existing owner SSH path.

No database credential or driver is needed, fetched or retained. The existing
SSH secret and ephemeral Actions token stay outside code/history and public output.
"""
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

from ops import light_native_service_history as h

REPOSITORY = 'olegmed1-art/bridge-video-free'
HOST = 'ubuntu@92.5.47.149'
QUALIFICATION = 'docs/operations/native-dormant-install-i2-20261019.md'


def inputs(event, source):
    value = json.loads(Path(event).read_bytes())['inputs']
    h.require(type(value) is dict and set(value) == {'expected_main_sha', 'accepted_package_sha256',
        'accepted_action_sha256', 'action_json'} and value['expected_main_sha'] == source
        and type(value['action_json']) is str, 'NATIVE_INSTALL_DISPATCH')
    raw = value['action_json'].encode('ascii')
    h.require(h.sha(raw) == value['accepted_action_sha256'], 'NATIVE_INSTALL_NOT_ACCEPTED')
    action = h.parse(raw)
    h.require(type(action) is dict and action.get('controller_source') == source
        and action.get('package_sha256') == value['accepted_package_sha256'], 'NATIVE_INSTALL_DISPATCH')
    return raw, value['accepted_action_sha256'], action


def context():
    source = os.environ.get('GITHUB_SHA')
    h.require(type(source) is str and re.fullmatch('[0-9a-f]{40}', source)
        and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
        and os.environ.get('GITHUB_REF') == 'refs/heads/main'
        and os.environ.get('GITHUB_ACTOR') == os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art'
        and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'NATIVE_INSTALL_CONTEXT')
    run, attempt = (os.environ.get(key, '') for key in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'))
    h.require(re.fullmatch('[1-9][0-9]{0,19}', run) and re.fullmatch('[1-9][0-9]{0,5}', attempt),
              'NATIVE_INSTALL_CONTEXT')
    return source, int(run), int(attempt)


def bootstrap(source, controller_pin, target_pin, action_pin, run, attempt):
    h.require(re.fullmatch('[0-9a-f]{40}', source) and all(re.fullmatch('[0-9a-f]{64}', pin)
              for pin in (controller_pin, target_pin, action_pin))
              and type(run) is type(attempt) is int and run > 0 and attempt > 0,
              'NATIVE_INSTALL_BOOTSTRAP')
    # Only reviewed names/digests/IDs are interpolated into code. All bytes and
    # the token travel over stdin, never a command argument, env file or history.
    return '''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 assert os.geteuid()==0 and os.name=='posix'
 def pairs(items):
  out={}
  for k,v in items:
   assert k not in out
   out[k]=v
  return out
 wire=sys.stdin.buffer.read(8*1024*1024+1)
 assert len(wire)<=8*1024*1024
 value=json.loads(wire,object_pairs_hook=pairs)
 assert type(value) is dict and set(value)=={'controller','target','action','token'}
 data={k:base64.b64decode(value[k],validate=True) for k in ('controller','target','action')}
 for k,pin in %r.items():assert hashlib.sha256(data[k]).hexdigest()==pin
 package=json.loads(data['controller'],object_pairs_hook=pairs)
 assert package['kind']=='LIGHT_NATIVE_CONTROL_SOURCE' and type(package['version']) is int and package['version']==1
 assert package['source']==%r and set(package['files'])==set(%r)
 blobs={}
 for name,item in package['files'].items():
  assert set(item)=={'sha256','base64'}
  blob=base64.b64decode(item['base64'],validate=True)
  assert 0<len(blob)<=262144 and hashlib.sha256(blob).hexdigest()==item['sha256']
  blobs[name]=blob
 with tempfile.TemporaryDirectory(prefix='native-dormant-install-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp); (root/'ops').mkdir(mode=0o700)
  for name,blob in {'ops/__init__.py':b'',**blobs}.items():
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'wb') as stream:stream.write(blob)
  sys.path.insert(0,str(root))
  from ops.light_native_service_install import InstallationRunBinding,operate
  from ops.native_maintenance_run_guard import API
  guard=InstallationRunBinding(%r,%d,%d,API(value['token']))
  guard.assert_running()
  result=operate(data['action'],%r,data['target'],guard)
  guard.assert_current()
  print(json.dumps(result,sort_keys=True),flush=True)
except BaseException:
 print('{"audit":"LIGHT_NATIVE_DORMANT_INSTALL_REFUSED","outcome_requires_reconciliation":true}',flush=True)
 raise SystemExit(2) from None
''' % (dict(controller=controller_pin, target=target_pin, action=action_pin), source, h.FILES,
       source, run, attempt, action_pin)


def main():
    h.require(len(sys.argv) == 3, 'NATIVE_INSTALL_ARGUMENTS')
    key, known = sys.argv[1:]
    source, run, attempt = context()
    repo = Path(__file__).resolve().parents[1]
    raw, pin, action = inputs(os.environ['GITHUB_EVENT_PATH'], source)
    controller = h.build(repo, source)
    target = h.build(repo, action['source'])
    h.package(target, action['package_sha256'])
    qualification = subprocess.check_output(['git', 'show', source+':'+QUALIFICATION], cwd=repo, timeout=10)
    h.require(h.sha(qualification) == action['qualification_sha256'], 'NATIVE_INSTALL_QUALIFICATION')
    # This guard uses the reviewed workflow/run/job contract and authenticated
    # exact main. Local observation cannot grant a different remote authority.
    from ops.light_native_service_install import InstallationRunBinding, action as validate
    from ops.native_maintenance_run_guard import API
    validate(raw, pin, target)
    guard = InstallationRunBinding(source, run, attempt, API(os.environ['GH_TOKEN']))
    guard.assert_running()
    code = bootstrap(source, h.sha(controller), h.sha(target), pin, run, attempt)
    wire = h.encoded(dict(controller=base64.b64encode(controller).decode(),
        target=base64.b64encode(target).decode(), action=base64.b64encode(raw).decode(),
        token=os.environ['GH_TOKEN']))
    argv = ['ssh', '-F', '/dev/null', '-i', key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+known,
        '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=2', HOST,
        shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-B', '-c', code])]
    guard.assert_current()
    result = subprocess.run(argv, input=wire, capture_output=True, timeout=285, env={'PATH': '/usr/bin:/bin'})
    h.require(result.returncode == 0 and 0 < len(result.stdout) <= 32768, 'NATIVE_INSTALL_OUTCOME_UNKNOWN')
    value = json.loads(result.stdout)
    h.require(type(value) is dict and value.get('audit') in
        ('LIGHT_NATIVE_DORMANT_INSTALLED', 'LIGHT_NATIVE_DORMANT_ROLLED_BACK')
        and value.get('source') == action['source'] and value.get('end_to_end_accepted') is False,
        'NATIVE_INSTALL_OUTCOME_UNKNOWN')
    guard.assert_current()
    print(json.dumps(value, sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print('{"audit":"LIGHT_NATIVE_DORMANT_INSTALL_REFUSED","outcome_requires_reconciliation":true}')
        raise SystemExit(2) from None
