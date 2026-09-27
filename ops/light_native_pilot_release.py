"""Immutable pilot release staging under the unchanged active Light HOLD.

This command cannot change the service configuration, native ACL/configuration,
root pilot admission, or shared queue. Source and bundle acceptance come from
the fixed reviewed runner, not from observed host state.
"""
import base64
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import tempfile

from ops import native_maintenance_bundle as source
from ops import oracle_light_active_hold_attest as hold
from ops import oracle_light_runtime_hold_install as staging

ROOT = Path('/var/lib/bridge-light-native-release')
PYTHON = '/opt/bridge-school/school-autopilot/.venv/bin/python'
CLOUD_ENVIRONMENT_ID = '6a97d5069f708191a28ed2b86f7fe5f1'
SUPERVISOR_HELPERS = ('ops/light_native_service_controller.py',
                     'ops/light_native_service_plan.py', 'ops/light_native_service_switch.py',
                     'ops/native_maintenance_agreement.py', 'ops/native_maintenance_workflow_pause.py')
EXTRA = ('database/__init__.py', 'database/native_cli_permission_engine.py',
         'ops/__init__.py', 'ops/native_permission_hold_guard.py',
         'ops/oracle_light_active_hold_attest.py', 'ops/oracle_autopilot_source_preflight.py',
         'ops/native_maintenance_bundle.py', 'ops/oracle_light_runtime_hold_install.py',
         'ops/light_native_pilot_release.py') + SUPERVISOR_HELPERS
HELPERS = ('ops/__init__.py', 'ops/native_maintenance_bundle.py',
           'ops/oracle_light_active_hold_attest.py', 'ops/oracle_light_runtime_hold_install.py',
           'ops/light_native_pilot_release.py') + SUPERVISOR_HELPERS
MARKERS = ('database/__init__.py', 'ops/__init__.py')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def bundle(repo, revision):
    require(source.identifier(revision, 40), 'PILOT_RELEASE_SOURCE')
    require(source.git(repo, 'cat-file', '-t', revision) == b'commit\n', 'PILOT_RELEASE_COMMIT')
    rows = source.git(repo, 'ls-tree', '-r', revision, '--', 'oracle_autopilot', 'autopilot_phase3b').decode().splitlines()
    files = {}
    for row in rows:
        mode, kind, object_id, path = row.split()
        if not path.endswith('.py'):
            continue
        require(mode == '100644' and kind == 'blob' and '..' not in Path(path).parts,
                'PILOT_RELEASE_MEMBER')
        files[path] = source.git(repo, 'cat-file', 'blob', object_id).decode()
    for path in EXTRA:
        if path in MARKERS:
            files[path] = ''
            continue
        entry = source.git(repo, 'ls-tree', revision, '--', path).decode().split()
        require(len(entry) == 4 and entry[0:2] == ['100644', 'blob'] and entry[3] == path,
                'PILOT_RELEASE_EXTRA')
        files[path] = source.git(repo, 'cat-file', 'blob', entry[2]).decode()
    require('oracle_autopilot/light_native_loader.py' in files and len(encoded(files)) < 2*1024*1024,
            'PILOT_RELEASE_INCOMPLETE')
    value = dict(revision=revision, files=files)
    return {**value, 'sha256': hashlib.sha256(encoded(value)).hexdigest()}


def validate(value, revision, accepted):
    require(type(value) is dict and set(value) == {'revision', 'files', 'sha256'}
            and value['revision'] == revision and source.identifier(revision, 40)
            and source.identifier(accepted, 64) and value['sha256'] == accepted,
            'PILOT_RELEASE_NOT_ACCEPTED')
    require(type(value['files']) is dict and len(encoded(value)) < 2*1024*1024,
            'PILOT_RELEASE_SIZE')
    require(hashlib.sha256(encoded({k: value[k] for k in ('revision', 'files')})).hexdigest() == accepted,
            'PILOT_RELEASE_DIGEST')
    for name, text in value['files'].items():
        require(type(name) is str and type(text) is str and not Path(name).is_absolute()
                and name == Path(name).as_posix() and '\\' not in name
                and '..' not in Path(name).parts and name.endswith('.py')
                and (name in EXTRA or name.startswith(('oracle_autopilot/', 'autopilot_phase3b/'))),
                'PILOT_RELEASE_PATH')
    require(set(EXTRA) <= set(value['files']) and 'oracle_autopilot/light_native_loader.py' in value['files'],
            'PILOT_RELEASE_INCOMPLETE')


def package(repo, revision):
    """All executable helpers and runtime bytes come from one committed tree."""
    helpers = {}
    for name in HELPERS:
        if name in MARKERS:
            helpers[name] = ''
            continue
        row = source.git(repo, 'ls-tree', revision, '--', name).decode().split()
        require(len(row) == 4 and row[:2] == ['100644', 'blob'] and row[3] == name,
                'PILOT_RELEASE_HELPER')
        helpers[name] = source.git(repo, 'cat-file', 'blob', row[2]).decode()
    raw = encoded(dict(version=1, source=revision, runtime=bundle(repo, revision), helpers=helpers))
    require(len(raw) < 3*1024*1024, 'PILOT_RELEASE_PACKAGE_SIZE')
    return raw


def program(raw, revision, accepted):
    require(source.identifier(revision, 40) and source.identifier(accepted, 64)
            and hashlib.sha256(raw).hexdigest() == accepted, 'PILOT_RELEASE_PACKAGE_NOT_ACCEPTED')
    # The reviewed checkout generates this bootstrap. Acceptance covers helper
    # code as well as runtime bytes; SSH host-key verification protects transit.
    return '''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 raw=base64.b64decode(%r,validate=True)
 assert hashlib.sha256(raw).hexdigest()==%r
 obj=json.loads(raw)
 assert obj['version']==1 and obj['source']==%r
 assert set(obj['helpers'])==set(%r)
 assert os.geteuid()==0
 with tempfile.TemporaryDirectory(prefix='light-pilot-bootstrap-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp)
  (root/'ops').mkdir(mode=0o700)
  for name,data in obj['helpers'].items():
   path=root/name
   fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream: stream.write(data)
  sys.path.insert(0,str(root))
  from ops.light_native_pilot_release import stage
  result=stage(obj['runtime'],obj['source'],obj['runtime']['sha256'])
  result['package_sha256']=%r
  print(json.dumps(result,sort_keys=True))
except BaseException:
 print('{"audit":"LIGHT_NATIVE_RELEASE_STAGE_REFUSED"}')
 sys.exit(2)
''' % (base64.b64encode(raw).decode(), accepted, revision, HELPERS, accepted)


def private_directory(path):
    row = path.lstat()
    require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and stat.S_IMODE(row.st_mode) == 0o700,
            'PILOT_RELEASE_PRIVATE_DIRECTORY')


def retain(path, raw):
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        staging.fsync_directory(path.parent)
    require(hold.read(path, 0o600, 4*1024*1024) == raw, 'PILOT_RELEASE_RETAINED_CONFLICT')


def probe(release):
    user = pwd.getpwnam('school-autopilot')
    disk = hold.read(hold.ENV, 0o600, 262144)
    # Existing strict environment parser, not a shell source operation.
    environment = hold.env(disk)
    child_env = {k: v for k, v in environment.items() if k.startswith('AUTOPILOT_')}
    child_env.update(PATH='/usr/bin:/bin', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1')
    program = '''import sys,json,os
sys.path.insert(0,sys.argv[1])
from oracle_autopilot import light_native_loader as loader
from oracle_autopilot import codex_cli_bridge as bridge
import psycopg
with psycopg.connect(**loader.runtime_parameters(os.environ['AUTOPILOT_DATABASE_URL'])) as conn:
 conn.read_only=True
 loader.runtime_identity(conn)
r=bridge.run_cli(['login','status'],timeout=15,profile='light')
assert r.returncode==0 and 'Logged in using ChatGPT' in (r.stdout+'\\n'+r.stderr).splitlines()
print('LIGHT_NATIVE_RELEASE_READONLY_READY')
'''
    def identity():
        os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    result = subprocess.run([PYTHON, '-I', '-B', '-c', program, str(release)], cwd=release,
        env=child_env, preexec_fn=identity, capture_output=True, timeout=45)
    require(result.returncode == 0 and result.stdout == b'LIGHT_NATIVE_RELEASE_READONLY_READY\n',
            'PILOT_RELEASE_PROBE_REFUSED')


def stage(value, revision, accepted):
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'PILOT_RELEASE_HOST')
    validate(value, revision, accepted)
    staging.require_current_main(revision)
    before = hold.attest()
    # New namespace creation is explicit staging, not a recovery operation.
    if not ROOT.exists():
        parent = ROOT.parent.lstat()
        require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == 0 and not parent.st_mode & 0o022,
                'PILOT_RELEASE_PARENT')
        ROOT.mkdir(mode=0o700); staging.fsync_directory(ROOT.parent)
    private_directory(ROOT)
    directory = ROOT / revision
    if not directory.exists():
        directory.mkdir(mode=0o700); staging.fsync_directory(ROOT)
    private_directory(directory)
    prior = dict(version=1, source=revision, bundle_sha256=accepted, hold=asdict(before),
        unit=base64.b64encode(hold.read(hold.BASE, 0o644, 65536)).decode(),
        drop=base64.b64encode(hold.read(hold.DROP, 0o644, 4096)).decode())
    raw = encoded(prior)
    retain(directory / 'before.json', raw)
    # Restore the retained bytes to an isolated private directory and compare
    # the exact files. This does not claim that a service restart was rehearsed.
    with tempfile.TemporaryDirectory(dir=directory) as temporary:
        restored = json.loads(hold.read(directory / 'before.json', 0o600, 262144))
        for name in ('unit', 'drop'):
            data = base64.b64decode(restored[name], validate=True)
            path = Path(temporary) / name
            retain(path, data)
            require(path.read_bytes() == base64.b64decode(prior[name]), 'PILOT_RELEASE_BACKUP_RESTORE')
    require(hold.attest() == before, 'PILOT_RELEASE_HOLD_CHANGED')
    staging.require_current_main(revision)
    release = staging.stage(value)
    probe(release)
    environment = probe_environment(release)
    retain(directory / 'environment.json', encoded(environment))
    staging.verify_release(release, value)
    require(hold.attest() == before, 'PILOT_RELEASE_HOLD_CHANGED')
    staging.require_current_main(revision)
    retain(directory / 'staged.json', encoded(dict(version=1, source=revision, bundle_sha256=accepted,
                                                   hold=asdict(before), readonly_probe=True)))
    return dict(audit='LIGHT_NATIVE_RELEASE_STAGED_UNDER_HOLD', source=revision, bundle_sha256=accepted,
                service_restarted=False, production_sql_mutations=False, hold_unchanged=True)


def probe_environment(candidate):
    """Read the exact Cloud environment using the actual service CLI profile.

    Listing never submits a task. Raw task contents remain inside the child;
    this receipt attests access, while repository mapping is checked separately.
    """
    user = pwd.getpwnam('school-autopilot')
    program = '''import hashlib,json,sys
sys.path.insert(0,sys.argv[1])
from oracle_autopilot import codex_cli_bridge as bridge
r=bridge.run_cli(['cloud','list','--env',sys.argv[2],'--limit','1','--json'],timeout=30,profile='light')
assert r.returncode==0 and 0<len(r.stdout.encode())<=1048576
value=json.loads(r.stdout)
assert type(value) in (dict,list)
print(hashlib.sha256(r.stdout.encode()).hexdigest())
'''
    def identity():
        os.setgroups([]); os.setgid(user.pw_gid); os.setuid(user.pw_uid)
    result = subprocess.run([PYTHON,'-I','-B','-c',program,str(candidate),CLOUD_ENVIRONMENT_ID],
        cwd=candidate,env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1'},
        preexec_fn=identity,capture_output=True,timeout=40)
    output = result.stdout.decode('ascii',errors='replace').strip()
    require(result.returncode == 0 and source.identifier(output,64), 'PILOT_ENVIRONMENT_ACCESS_REFUSED')
    return dict(version=1,source=candidate.name,environment_id=CLOUD_ENVIRONMENT_ID,
                service_user='school-autopilot',
                command='cloud list --env '+CLOUD_ENVIRONMENT_ID+' --limit 1 --json',
                output_sha256=output)


if __name__ == '__main__':
    if len(sys.argv) not in (3, 4) or sys.argv[1] not in ('digest', 'program'):
        raise SystemExit('usage: python -m ops.light_native_pilot_release digest SOURCE | program SOURCE ACCEPTED_SHA256')
    payload = package(Path.cwd(), sys.argv[2])
    if sys.argv[1] == 'digest' and len(sys.argv) == 3:
        print(hashlib.sha256(payload).hexdigest())
    elif sys.argv[1] == 'program' and len(sys.argv) == 4:
        print(program(payload, sys.argv[2], sys.argv[3]))
    else:
        raise SystemExit(2)
