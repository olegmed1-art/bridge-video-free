"""Generate one fixed same-task continuation over the verified SSH path."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from ops import native_maintenance_driver as driver
from ops.oracle_autopilot_source_preflight import connection_parameters

from ops import light_native_pilot_release as release

SOURCE = '8bbc1d61010ef70c3fca02b5151ac86fce02a144'
PACKAGE = '46acb2672ba58dc369c3ccf70f45bb4beff37cb4efad2bfe3b34ae4a170c0c94'
BRANCH = 'recovery/light-pilot-continuation-20260928'
MODULE = 'ops/incident/light_pilot_controls_cleanup_20260928.py'
WORKFLOW = '.github/workflows/light-native-pilot-owner.yml'
REPO = 'olegmed1-art/bridge-video-free'
OWNER_ID = 315099490


def program(wheel_directory):
    root = Path(__file__).resolve().parents[2]
    execution = os.environ['EXPECTED_MAIN']
    assert re.fullmatch('[0-9a-f]{40}', execution)
    assert os.environ['GITHUB_SHA'] == execution
    assert os.environ['GITHUB_WORKFLOW_SHA'] == execution
    assert os.environ['GITHUB_WORKFLOW_REF'] == REPO + '/' + WORKFLOW + '@refs/heads/' + BRANCH
    assert os.environ['GITHUB_JOB'] == 'step'
    assert re.fullmatch('[1-9][0-9]{0,19}', os.environ['GITHUB_RUN_ID'])
    assert os.environ['GITHUB_RUN_ATTEMPT'] == '1'
    assert os.environ['GITHUB_REF'] == 'refs/heads/' + BRANCH
    assert os.environ['GITHUB_EVENT_NAME'] == 'workflow_dispatch'
    assert os.environ['GITHUB_ACTOR'] == os.environ['GITHUB_TRIGGERING_ACTOR'] == 'olegmed1-art'
    assert os.environ['GITHUB_REPOSITORY'] == REPO
    assert os.environ['PILOT_ACCEPTED_PACKAGE'] == PACKAGE
    accepted_payload = os.environ['PILOT_ACCEPTED_PAYLOAD']
    assert re.fullmatch('[0-9a-f]{64}', accepted_payload)
    credential = os.environ.pop('NATIVE_OWNER_DATABASE_URL')
    connection_parameters(credential, 'neondb_owner')
    wheels = driver.build(wheel_directory)
    token = os.environ['GH_TOKEN']
    assert 0 < len(token) <= 4096
    package = release.package(root, SOURCE)
    assert hashlib.sha256(package).hexdigest() == PACKAGE
    payload = base64.b64decode(os.environ['PILOT_PAYLOAD_BASE64'], validate=True)
    assert hashlib.sha256(payload).hexdigest() == accepted_payload
    code = subprocess.check_output(['git','show',execution+':'+MODULE],cwd=root)
    assert code == (root/MODULE).read_bytes()
    assert (root/'ops/incident/light_pilot_controls_cleanup_runner_20260928.py').read_bytes() == subprocess.check_output(
        ['git','show',execution+':ops/incident/light_pilot_controls_cleanup_runner_20260928.py'],cwd=root)
    workflow = subprocess.check_output(['git','show',execution+':'+WORKFLOW],cwd=root)
    assert workflow == (root/WORKFLOW).read_bytes()
    return '''import base64,hashlib,json,os,pathlib,re,sys,tempfile,types,time
phase='input'
try:
 assert os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic'
 raw=base64.b64decode(%r,validate=True)
 assert hashlib.sha256(raw).hexdigest()==%r
 package=json.loads(raw)
 assert package['source']==%r
 payload=base64.b64decode(%r,validate=True)
 assert hashlib.sha256(payload).hexdigest()==%r
 accepted_helpers=set(%r)
 assert set(package['helpers'])==accepted_helpers
 with tempfile.TemporaryDirectory(prefix='light-continuation-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp);(root/'ops').mkdir(mode=0o700)
  for name,value in package['helpers'].items():
   path=pathlib.Path(name)
   assert name in accepted_helpers and path.parts==('ops',path.name) and path.suffix=='.py'
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream:stream.write(value)
  sys.path.insert(0,str(root))
  from ops import light_native_pilot_release as original
  from ops.native_maintenance_run_guard import API
  assert set(package['helpers'])==set(original.HELPERS)
  repair=types.ModuleType('fixed_continuation')
  exec(compile(base64.b64decode(%r),'<fixed-continuation>','exec'),repair.__dict__)
  sys.path.append('/opt/bridge-school/school-autopilot-production-light/releases/'+%r)
  wheels=base64.b64decode(%r,validate=True)
  assert hashlib.sha256(wheels).hexdigest()==%r
  credential=%r
  token=%r
  api=API(token)
  run_id=%r
  execution=%r
  workflow_sha=%r
  observed_job=[None]
  def live_guard():
   run=api.get('/actions/runs/'+str(run_id))
   assert run['id']==run_id and run['run_attempt']==1 and run['head_sha']==execution
   assert run['head_branch']==%r and run['event']=='workflow_dispatch'
   assert run['status']=='in_progress' and run['conclusion'] is None and run['path']==%r
   assert run['repository']['full_name']==run['head_repository']['full_name']==%r
   assert all(run[k]['login']=='olegmed1-art' and run[k]['id']==%r
              for k in ('actor','triggering_actor'))
   jobs=api.get('/actions/runs/'+str(run_id)+'/attempts/1/jobs?per_page=100')
   rows=jobs['jobs']
   assert jobs['total_count']==2 and len(rows)==2 and {j['name'] for j in rows}=={'contract','step'}
   assert len({j['id'] for j in rows})==2
   assert all(j['run_id']==run_id and j['run_attempt']==1 and j['head_sha']==execution
              for j in rows)
   assert any(j['name']=='contract' and j['status']=='completed' and j['conclusion']=='success' for j in rows)
   job=next(j for j in rows if j['name']=='step')
   assert job['status']=='in_progress' and job['conclusion'] is None
   assert observed_job[0] is None or observed_job[0]==job['id']
   main=api.get('/git/ref/heads/main')
   assert main['ref']=='refs/heads/main' and main['object']['type']=='commit' and main['object']['sha']==%r
   blob=api.get('/contents/'+%r+'?ref='+execution)
   assert blob['type']=='file' and blob['path']==%r and blob['encoding']=='base64'
   assert 0<blob['size']<=32768
   data=base64.b64decode(blob['content'].replace('\\n',''),validate=True)
   assert len(data)==blob['size'] and hashlib.sha256(data).hexdigest()==workflow_sha
   observed_job[0]=job['id']
  deadline=time.monotonic()+85
  class RunGuard:
   def assert_current(self):
    assert time.monotonic()<deadline
   def assert_running(self):
    self.assert_current()
    live_guard()
    self.assert_current()
  phase='continuation'
  result=repair.reconcile(raw,payload,%r,wheels,credential,token,RunGuard())
  print(json.dumps(result,sort_keys=True))
except BaseException as exc:
 value=exc.args[0] if len(exc.args)==1 and type(exc.args[0]) is str else ''
 code=value if re.fullmatch('[A-Z][A-Z0-9_]{1,100}',value) else type(exc).__name__
 print(json.dumps({'audit':'LIGHT_PILOT_CLEANUP_REFUSED','phase':phase,'code':code},sort_keys=True))
 raise SystemExit(2) from None
''' % (base64.b64encode(package).decode(), PACKAGE, SOURCE,
       base64.b64encode(payload).decode(), accepted_payload, release.HELPERS,
       base64.b64encode(code).decode(), SOURCE, base64.b64encode(wheels).decode(), driver.sha(wheels), credential, token, int(os.environ['GITHUB_RUN_ID']), execution,
       hashlib.sha256(workflow).hexdigest(), BRANCH, WORKFLOW, REPO, OWNER_ID,
       SOURCE, WORKFLOW, WORKFLOW, accepted_payload)


def main():
    assert len(sys.argv) == 4
    key, known_hosts, wheel_directory = sys.argv[1:]
    code = program(wheel_directory).encode()
    assert len(code) < 20*1024*1024
    command = ['ssh','-F','/dev/null','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes',
        '-o','ForwardAgent=no','-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+known_hosts,
        '-o','ConnectTimeout=15','-o','ConnectionAttempts=1','ubuntu@92.5.47.149',
        'sudo -n /usr/bin/timeout --signal=KILL 90 /usr/bin/python3 -I -S -B -']
    result = subprocess.run(command,input=code,capture_output=True,timeout=110,env={'PATH':'/usr/bin:/bin'})
    assert len(result.stdout) <= 262144
    if result.stdout:
        print(result.stdout.decode(),end='')
    if result.returncode != 0:
        raise RuntimeError('CONTINUATION_REMOTE_REFUSED')


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print('{"audit":"LIGHT_PILOT_CLEANUP_RUNNER_REFUSED"}')
        raise SystemExit(2) from None
