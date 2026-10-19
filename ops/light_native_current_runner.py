"""Fixed authenticated current observation; no DB credential, driver or write port."""
import ast
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

REFUSED = {'audit': 'LIGHT_NATIVE_CURRENT_RECONCILIATION_REFUSED'}


def bootstrap(source, controller_pin, runtime_pin, helpers_pin, legacy_pin, payload_pin, run, attempt):
    for value, size in ((source, 40), *((pin, 64) for pin in
            (controller_pin, runtime_pin, helpers_pin, legacy_pin, payload_pin))):
        if type(value) is not str or re.fullmatch('[0-9a-f]{'+str(size)+'}', value) is None:
            raise ValueError('CURRENT_BOOTSTRAP_BINDING')
    if not (type(run) is int and 1 <= run < 10**20 and type(attempt) is int and 1 <= attempt < 10**6):
        raise ValueError('CURRENT_BOOTSTRAP_BINDING')
    return '''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 assert os.geteuid()==0 and os.uname().nodename=='autopilot-lite-vnic'
 wire=sys.stdin.buffer.read(16*1024*1024+1)
 assert len(wire)<=16*1024*1024
 envelope=json.loads(wire)
 assert type(envelope) is dict and set(envelope)=={'controller','runtime','capture_helpers','legacy_package','payload','token'}
 assert json.dumps(envelope,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()==wire
 assert type(envelope['token']) is str and 0<len(envelope['token'])<=4096
 decoded={key:base64.b64decode(envelope[key],validate=True) for key in ('controller','runtime','capture_helpers','legacy_package','payload')}
 for key,pin in %r.items():assert hashlib.sha256(decoded[key]).hexdigest()==pin
 package=json.loads(decoded['controller'])
 extra=json.loads(decoded['capture_helpers'])
 assert type(package) is dict and set(package)=={'version','kind','source','helpers'}
 assert type(package['version']) is int and package['version']==1 and package['kind']=='LIGHT_LANE_CONTROLLER' and package['source']==%r
 assert type(extra) is dict and set(extra)=={'version','kind','source','helpers'}
 assert type(extra['version']) is int and extra['version']==1 and extra['kind']=='CURRENT_RECONCILIATION_HELPERS' and extra['source']==%r
 assert type(package['helpers']) is dict and type(extra['helpers']) is dict
 assert set(extra['helpers'])==%r and not set(extra['helpers']).intersection(package['helpers'])
 helpers={**package['helpers'],**extra['helpers']}
 assert 1<=len(helpers)<=128
 for name,text in helpers.items():
  assert type(name) is str and len(name)<200 and name.count('/')==1 and name.endswith('.py')
  assert name.split('/')[0] in ('ops','database') and all(part not in ('','..','.') for part in name.split('/'))
  assert type(text) is str and len(text.encode())<=1024*1024
 with tempfile.TemporaryDirectory(prefix='light-native-current-',dir='/var/tmp') as temporary:
  root=pathlib.Path(temporary)
  assert root.stat().st_uid==0 and root.stat().st_mode&0o777==0o700
  for name in ('ops','database'):(root/name).mkdir(mode=0o700)
  for name,text in helpers.items():
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w',encoding='utf-8',newline='') as stream:stream.write(text)
  sys.path.insert(0,str(root))
  sys.path.append('/opt/bridge-school/school-autopilot-production-light/releases/f82d58efabf21ba62fd242a4fa02a8e7cfda1d23')
  from ops import light_native_reconciliation as current
  from ops.light_native_lane_controller import validate_package
  from ops.light_native_lane_run_guard import authenticated
  validate_package(decoded['controller'],%r,%r)
  reference=current.reference(decoded['payload'],%r)
  assert reference['source']==%r and reference['accepted_controller_sha256']==%r and reference['accepted_runtime_sha256']==%r
  assert reference['accepted_capture_helpers_sha256']==%r and reference['accepted_legacy_package_sha256']==%r
  guard=authenticated(%r,%d,%d,envelope['token'],read_only=True)
  result=current.observe_current(decoded['controller'],decoded['runtime'],decoded['capture_helpers'],decoded['legacy_package'],decoded['payload'],%r,guard)
  guard.assert_running()
  checked=current.public_result(result,reference)
  print(json.dumps(checked,sort_keys=True))
  status=0 if checked['state']=='OBSERVED_CURRENT' else 2
except BaseException:
 print('{"audit":"LIGHT_NATIVE_CURRENT_RECONCILIATION_REFUSED"}')
 status=2
raise SystemExit(status)
''' % (dict(controller=controller_pin, runtime=runtime_pin, capture_helpers=helpers_pin,
            legacy_package=legacy_pin, payload=payload_pin), source, source,
        {'ops/light_native_legacy_capture.py', 'ops/light_native_current_anchor.py',
         'ops/light_native_reconciliation.py', 'ops/oracle_light_stopped_hold_upgrade.py'},
        source, controller_pin, payload_pin, source, controller_pin, runtime_pin, helpers_pin,
        legacy_pin, source, run, attempt, payload_pin)


def dispatch_inputs(event_path, source):
    from ops import light_native_reconciliation as current
    from ops import light_native_retirement as r
    event = r.parse(Path(event_path).read_bytes(), limit=1024*1024)
    inputs = event['inputs']
    r.need(type(inputs) is dict and set(inputs) == {'expected_main_sha', 'accepted_controller_sha256',
        'accepted_runtime_sha256', 'accepted_payload_sha256', 'retirement_reference_json'}
        and inputs['expected_main_sha'] == source, 'CURRENT_REFERENCE')
    raw = inputs['retirement_reference_json'].encode('utf-8')
    reference = current.reference(raw, inputs['accepted_payload_sha256'])
    r.need(reference['source'] == source and all(reference[key] == inputs[key] for key in
        ('accepted_controller_sha256', 'accepted_runtime_sha256')), 'CURRENT_REFERENCE')
    return raw, reference


def helper_package(repo, source):
    from ops import light_native_reconciliation as current
    from ops import light_native_pilot_release as release
    return current.encoded(dict(version=1, kind='CURRENT_RECONCILIATION_HELPERS', source=source,
        helpers={name: release.source.git(repo, 'show', source+':'+name).decode('utf-8')
                 for name in sorted(current.HELPERS)}))


def _literal(raw, name):
    tree = ast.parse(raw)
    matches = [node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    if len(matches) != 1:
        raise ValueError('CURRENT_LEGACY_CONSTANT')
    return ast.literal_eval(matches[0])


def legacy_package(repo, source):
    """Reproduce the installed bundle using Git data, without importing old code."""
    from ops import light_native_reconciliation as current
    from ops import light_native_pilot_release as release
    git = lambda *args: release.source.git(repo, *args)
    resume = git('show', source+':ops/oracle_light_resume.py')
    revision, logical = _literal(resume, 'INSTALLED'), _literal(resume, 'BUNDLE_SHA')
    if (revision, logical) != (current.LEGACY_SOURCE, current.LEGACY_BUNDLE):
        raise ValueError('CURRENT_LEGACY_BINDING')
    names = git('ls-tree', '-r', '--name-only', revision, 'oracle_autopilot', 'autopilot_phase3b').decode().splitlines()
    names = [name for name in names if name.count('/') == 1 and name.endswith('.py')]
    names.append('ops/autopilot/broker-release.json')
    files = {name: git('show', revision+':'+name).decode('utf-8') for name in names}
    keys = _literal(git('show', source+':ops/oracle_light_stopped_hold_upgrade.py'), 'PIN_KEYS')
    old = _literal(git('show', source+':ops/oracle_light_held_runtime_preflight.py'), 'REVISION')
    manifest = json.loads(files['ops/autopilot/broker-release.json'])
    pins = {key: manifest[field] for key, field in keys.items()}
    files['ops/autopilot/broker-hold.env'] = ''.join(key+'='+pins[key]+'\n' for key in sorted(pins))
    files['ops/autopilot/previous-hold.conf'] = '[Service]\nWorkingDirectory=/opt/bridge-school/school-autopilot-production-light/releases/'+old+'\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'
    bundle = dict(revision=revision, files=files)
    bundle['sha256'] = current.sha(current._legacy_encoded(bundle))
    if bundle['sha256'] != logical:
        raise ValueError('CURRENT_LEGACY_BUNDLE')
    raw = current._legacy_encoded(bundle)
    current.validate_legacy_package(raw, current.sha(raw), logical, revision)
    return raw


def checked_output(raw, returncode, reference):
    from ops import light_native_reconciliation as current
    from ops import light_native_retirement as r
    r.need(type(raw) is bytes and 0 < len(raw) <= 4096, 'CURRENT_OUTPUT')
    try:
        value = r.parse(raw, limit=4096)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise current.Refusal('FINAL_GUARDS') from None
    if returncode and value == REFUSED:
        return value, 2
    checked = current.public_result(value, reference)
    r.need((returncode == 0) == (checked['state'] == 'OBSERVED_CURRENT'), 'CURRENT_OUTPUT')
    return checked, 0 if returncode == 0 else 2


def main():
    from ops import light_native_reconciliation as current
    from ops import light_native_lane_controller as owner
    from ops import light_native_pilot_release as release
    from ops.light_native_lane_run_guard import local_context
    from ops.native_maintenance_store_runner import HOST, source_check
    current._need(len(sys.argv) == 3)
    key, known = sys.argv[1:]
    source, run, attempt = local_context(os.environ, read_only=True)
    payload, reference = dispatch_inputs(os.environ['GITHUB_EVENT_PATH'], source)
    source_check(source)
    repo = Path(__file__).resolve().parents[1]
    blobs = dict(controller=owner.package(repo, source),
        runtime=release.package(repo, owner.install.RETAINED_SOURCE),
        capture_helpers=helper_package(repo, source), legacy_package=legacy_package(repo, source), payload=payload)
    pins = [reference[key] for key in ('accepted_controller_sha256', 'accepted_runtime_sha256',
        'accepted_capture_helpers_sha256', 'accepted_legacy_package_sha256')]+[current.sha(payload)]
    current._need([current.sha(raw) for raw in blobs.values()] == pins, 'PACKAGES')
    wire = current.encoded(dict(**{key: base64.b64encode(raw).decode() for key, raw in blobs.items()},
        token=os.environ['GH_TOKEN']))
    code = bootstrap(source, *pins, run, attempt)
    command = ['ssh', '-F', '/dev/null', '-i', key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
        '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile='+known,
        '-o', 'ConnectTimeout=15', '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=10',
        '-o', 'ServerAliveCountMax=2', HOST,
        shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-S', '-B', '-c', code])]
    source_check(source)
    result = subprocess.run(command, input=wire, capture_output=True, timeout=150, env={'PATH': '/usr/bin:/bin'})
    checked, status = checked_output(result.stdout, result.returncode, reference)
    source_check(source)
    print(json.dumps(checked, sort_keys=True))
    return status


if __name__ == '__main__':
    try:
        status = main()
    except BaseException:
        print(json.dumps(REFUSED, sort_keys=True))
        raise SystemExit(2) from None
    raise SystemExit(status)
