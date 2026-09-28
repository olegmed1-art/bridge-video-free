"""Install and rehearse a credential-free, disabled-on-boot native HOLD unit.

No RUN transition, permit issuer, queue mutation, legacy restart or reset API.
Interrupted installations remain retained and require explicit reconciliation.
"""
import base64
from dataclasses import asdict
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys
import time

from ops import light_native_pilot_release as release
from ops import light_native_service_plan as plan
from ops import light_native_service_switch as switch
from ops import oracle_light_active_hold_attest as hold

require = release.require
UNIT = 'school-autopilot-native-light.service'
UNIT_FILE = Path('/etc/systemd/system') / UNIT
CONTROL = Path('/etc/bridge-school/light-native-lane')
STATE = plan.LIGHT / 'runtime/native-lane'
LEDGER = Path('/var/lib/bridge-light-native-lane-install')
PROC = Path('/proc')
RETAINED_SOURCE = 'f82d58efabf21ba62fd242a4fa02a8e7cfda1d23'
LAUNCH = 'import sys;sys.path.insert(0,sys.argv[1]);from oracle_autopilot.light_native_lane import main;main()'


def render(source):
    candidate = plan.source_path(source)
    properties = {**plan.HARDENING, 'Type': 'exec', 'WorkingDirectory': str(candidate),
        'ReadWritePaths': str(STATE), 'PrivateNetwork': 'yes',
        'Environment': 'PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AUTOPILOT_ADMISSION_MODE=HOLD',
        'ExecStart': release.PYTHON + ' -I -B -c "' + LAUNCH + '" ' + str(candidate)}
    return ('[Unit]\nDescription=Bridge native lane dormant HOLD\n\n[Service]\n' +
            ''.join(key+'='+value+'\n' for key,value in properties.items())).encode()


def root_parent(path):
    """Refuse every writable/non-root/symlink ancestor before root writes."""
    for parent in (path, *path.parents):
        row = parent.lstat()
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022,
                'LANE_INSTALL_PARENT')


def fresh_directory(path, mode, uid=0, gid=0):
    root_parent(path.parent)
    path.mkdir(mode=mode)  # no reuse, including partial/uncertain earlier attempts
    os.chown(path, uid, gid, follow_symlinks=False)
    os.chmod(path, mode, follow_symlinks=False)
    release.staging.fsync_directory(path.parent)


def fresh_state(user):
    # The existing runtime parent belongs to the service. Never chown/chmod a
    # service-controlled path as root: create it after irrevocably dropping uid.
    def identity():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)
    program = "import os,sys;from pathlib import Path;p=Path(sys.argv[1]);p.mkdir(mode=0o700);f=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW);os.fsync(f);os.close(f)"
    result = subprocess.run(['/usr/bin/python3','-I','-S','-B','-c',program,str(STATE)],
        env={'PATH':'/usr/bin:/bin'},preexec_fn=identity,capture_output=True,timeout=10)
    require(result.returncode == 0, 'LANE_INSTALL_STATE_CREATE')


def write_new(path, raw, mode, gid=0):
    root_parent(path.parent)
    fd = os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    release.staging.fsync_directory(path.parent)
    require(hold.read(path, mode, len(raw)+1) == raw, 'LANE_INSTALL_READBACK')


def show(unit, fields):
    # systemd 255 omits the empty EnvironmentFiles array even with --all.
    # Read that typed property directly; never infer emptiness from omission.
    require(unit == UNIT, 'LANE_INSTALL_UNIT_SCOPE')
    selected = [key for key in fields if key != 'EnvironmentFiles']
    output = (switch.command('/usr/bin/systemctl','show','--all',unit,
                             *['--property='+key for key in selected]) if selected else '')
    result = {}
    for line in output.splitlines():
        key, sep, value = line.partition('=')
        require(sep and key in selected and key not in result, 'LANE_INSTALL_UNIT_FIELDS')
        result[key] = value
    require(set(result) == set(selected), 'LANE_INSTALL_UNIT_FIELDS')
    if 'EnvironmentFiles' in fields:
        value = switch.command('/usr/bin/busctl','get-property','org.freedesktop.systemd1',
            '/org/freedesktop/systemd1/unit/school_2dautopilot_2dnative_2dlight_2eservice',
            'org.freedesktop.systemd1.Service','EnvironmentFiles')
        require(value == 'a(sb) 0', 'LANE_INSTALL_ENVIRONMENT_FILES')
        result['EnvironmentFiles'] = ''
    return result


def stopped():
    row = show(UNIT, ['MainPID','ControlPID','ActiveState','ControlGroup'])
    require(row['MainPID'] == row['ControlPID'] == '0'
            and row['ActiveState'] in ('inactive','failed'), 'LANE_INSTALL_PROCESS_REMAINS')
    expected = '/system.slice/'+UNIT
    require(row['ControlGroup'] in ('', expected), 'LANE_INSTALL_CGROUP')
    path = Path('/sys/fs/cgroup') / expected.lstrip('/')
    if path.exists():
        events = dict(line.split() for line in (path/'cgroup.events').read_text().splitlines())
        require(events.get('populated') == '0', 'LANE_INSTALL_CGROUP_POPULATED')


def verify_config(source, gid):
    root_parent(UNIT_FILE.parent)
    root_parent(CONTROL)
    require(hold.read(UNIT_FILE, 0o644, 8192) == render(source), 'LANE_INSTALL_UNIT_CHANGED')
    require(hold.read(CONTROL/'admission', 0o640, 16) == b'HOLD\n'
            and (CONTROL/'admission').lstat().st_gid == gid
            and set(os.listdir(CONTROL)) == {'admission','jobs'}
            and not os.listdir(CONTROL/'jobs'), 'LANE_INSTALL_ADMISSION_CHANGED')
    root_parent(CONTROL/'jobs')
    fields = {**plan.HARDENING, 'ReadWritePaths': str(STATE), 'PrivateNetwork': 'yes',
        'Environment': 'PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AUTOPILOT_ADMISSION_MODE=HOLD',
        'EnvironmentFiles': '', 'WorkingDirectory': str(plan.source_path(source)),
        'Type': 'exec', 'FragmentPath': str(UNIT_FILE), 'DropInPaths': '',
        'NeedDaemonReload': 'no', 'UnitFileState': 'static'}
    fields.pop('CPUQuota'); fields.pop('TimeoutStopSec')
    fields.update(CPUQuotaPerSecUSec='1s', TimeoutStopUSec='30s',
                  MemoryHigh='536870912', MemoryMax='805306368')
    require(show(UNIT, list(fields)) == fields, 'LANE_INSTALL_LOADED_CONFIG')
    command = show(UNIT,['ExecStart'])['ExecStart']
    expected = '{ path='+release.PYTHON+' ; argv[]='+release.PYTHON+' -I -B -c '+LAUNCH+' '+str(plan.source_path(source))+' ; ignore_errors=no ;'
    require(command.startswith(expected) and command.endswith(' }')
            and command.count('{') == command.count('}') == 1, 'LANE_INSTALL_EXECUTION')


def verify_running(source, user):
    verify_config(source, user.pw_gid)
    row = show(UNIT, ['ActiveState','SubState','MainPID','NRestarts','InvocationID'])
    require(row['ActiveState'] == 'active' and row['SubState'] == 'running'
            and row['NRestarts'] == '0' and row['MainPID'].isdigit()
            and int(row['MainPID']) > 1, 'LANE_INSTALL_NOT_RUNNING')
    require(len(row['InvocationID']) == 32 and all(c in '0123456789abcdef' for c in row['InvocationID']),
            'LANE_INSTALL_INVOCATION')
    process = PROC / row['MainPID']
    require(process.stat().st_uid == user.pw_uid
            and process.joinpath('cwd').resolve() == plan.source_path(source), 'LANE_INSTALL_PROCESS_IDENTITY')
    require(process.joinpath('cmdline').read_bytes().split(b'\0') == [x.encode() for x in
        [release.PYTHON,'-I','-B','-c',LAUNCH,str(plan.source_path(source)),'']], 'LANE_INSTALL_PROCESS_COMMAND')
    environment = dict(item.split(b'=',1) for item in process.joinpath('environ').read_bytes().split(b'\0') if item)
    safe = {b'PATH',b'LANG',b'LANGUAGE',b'USER',b'LOGNAME',b'HOME',b'SHELL',b'INVOCATION_ID',
            b'JOURNAL_STREAM',b'SYSTEMD_EXEC_PID',b'MEMORY_PRESSURE_WATCH',b'MEMORY_PRESSURE_WRITE',
            b'PYTHONDONTWRITEBYTECODE',b'PYTHONUNBUFFERED',b'AUTOPILOT_ADMISSION_MODE'}
    require(all(key in safe or key.startswith(b'LC_') for key in environment)
            and environment.get(b'AUTOPILOT_ADMISSION_MODE') == b'HOLD', 'LANE_INSTALL_PROCESS_ENVIRONMENT')
    info = STATE.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == user.pw_uid
            and stat.S_IMODE(info.st_mode) == 0o700 and set(os.listdir(STATE)) == {'pilot.lock'},
            'LANE_INSTALL_STATE_CHANGED')
    records = switch.command('/usr/bin/journalctl', '--no-pager', '-o', 'cat', '-n', '20',
                             '_SYSTEMD_INVOCATION_ID='+row['InvocationID'])
    expected = '{"audit":"LIGHT_NATIVE_LANE","state":"HOLD"}'
    require(records.splitlines() == [expected], 'LANE_INSTALL_HOLD_NOT_OBSERVED')
    require(show(UNIT, list(row)) == row, 'LANE_INSTALL_PROCESS_CHANGED')
    return row


def await_hold(source, user):
    # Wait for the first journal entry, with a small bounded startup allowance.
    for _ in range(20):
        try:
            return verify_running(source, user)
        except (RuntimeError, FileNotFoundError):
            time.sleep(0.5)
    return verify_running(source, user)


def rehearse(source, controller_source, prior, user):
    start_attempted = False
    try:
        release.staging.require_current_main(controller_source)
        start_attempted = True
        switch.command('/usr/bin/systemctl','start',UNIT)
        first = await_hold(source,user)
        switch.command('/usr/bin/systemctl','stop',UNIT)
        stopped()
        require(hold.attest() == prior, 'LANE_INSTALL_LEGACY_CHANGED')
        release.staging.require_current_main(controller_source)
        write_new(LEDGER/'stop-rehearsal.json',release.encoded(dict(version=1,source=source,
            first_invocation=first['InvocationID'],cgroup_empty=True,legacy_unchanged=True)),0o600)
        release.staging.require_current_main(controller_source)
        start_attempted = True
        switch.command('/usr/bin/systemctl','start',UNIT)
        second = await_hold(source,user)
        require(first['InvocationID'] != second['InvocationID'], 'LANE_INSTALL_REENTRY_NOT_PROVEN')
        require(hold.attest() == prior, 'LANE_INSTALL_LEGACY_CHANGED')
        release.staging.require_current_main(controller_source)
        write_new(LEDGER/'installed.json',release.encoded(dict(version=1,source=source,
            unit_sha256=release.hashlib.sha256(render(source)).hexdigest(),
            invocation=second['InvocationID'],stop_rehearsal=True)),0o600)
        return dict(state='HOLD',stop_rehearsal=True,**second)
    except BaseException:
        if start_attempted:
            switch.command('/usr/bin/systemctl','stop',UNIT)
            stopped()
        raise


def operation(bundle, source, action, *, controller_source=None):
    controller_source = controller_source or source
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'LANE_INSTALL_HOST')
    require(action in ('install-hold','observe-hold','stop-hold','complete-hold','inspect-hold'), 'LANE_INSTALL_ACTION')
    release.validate(bundle, source, bundle['sha256'])
    require(action != 'install-hold' or controller_source == source, 'LANE_INSTALL_CONTROLLER_SOURCE')
    release.staging.require_current_main(controller_source)
    prior = hold.attest()
    user = pwd.getpwnam('school-autopilot')
    if action != 'install-hold':
        root_parent(LEDGER)
        receipt = json.loads(hold.read(LEDGER/'before.json',0o600,262144))
        expected = dict(version=1,source=source,bundle_sha256=bundle['sha256'],
                        legacy_hold=asdict(prior),prior_native='absent')
        if receipt != expected:
            same_identity = {**expected, 'legacy_hold': {**asdict(prior),
                'fingerprint': receipt.get('legacy_hold',{}).get('fingerprint')}}
            require(receipt != same_identity, 'LANE_INSTALL_LEGACY_FINGERPRINT_CHANGED')
            require(False, 'LANE_INSTALL_PRIOR_CHANGED')
        release.staging.verify_release(plan.source_path(source), bundle)
        verify_config(source,user.pw_gid)
        if action in ('complete-hold','inspect-hold'):
            require(source == RETAINED_SOURCE and set(os.listdir(LEDGER)) == {'before.json'},
                    'LANE_INSTALL_REENTRY_LEDGER')
            require(show(UNIT,['InvocationID','ExecMainPID','MainPID','ControlPID','ActiveState']) ==
                    dict(InvocationID='',ExecMainPID='0',MainPID='0',ControlPID='0',ActiveState='inactive'),
                    'LANE_INSTALL_REENTRY_ALREADY_STARTED')
            info = STATE.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == user.pw_uid
                    and stat.S_IMODE(info.st_mode) == 0o700 and not os.listdir(STATE),
                    'LANE_INSTALL_REENTRY_STATE')
            stopped()
            require(hold.attest() == prior, 'LANE_INSTALL_LEGACY_CHANGED')
            result = (dict(state='READY_TO_COMPLETE') if action == 'inspect-hold' else
                      rehearse(source,controller_source,prior,user))
        elif action == 'stop-hold':
            release.staging.require_current_main(controller_source)
            switch.command('/usr/bin/systemctl','stop',UNIT)
            stopped()
            result = {'state':'STOPPED'}
        else:
            result = dict(state='HOLD', **verify_running(source,user))
    else:
        switch.unit_absent(UNIT)
        require(not any(p.exists() or p.is_symlink() for p in (LEDGER, CONTROL, STATE, UNIT_FILE)),
                'LANE_INSTALL_REQUIRES_RECONCILIATION')
        # Stage performs read-only DB/profile probes; idle service gets no credentials.
        release.stage(bundle,source,bundle['sha256'])
        require(hold.attest() == prior, 'LANE_INSTALL_LEGACY_CHANGED')
        release.staging.require_current_main(source)
        fresh_directory(LEDGER,0o700)
        write_new(LEDGER/'before.json',release.encoded(dict(version=1,source=source,
            bundle_sha256=bundle['sha256'],legacy_hold=asdict(prior),prior_native='absent')),0o600)
        try:
            fresh_directory(CONTROL,0o750,gid=user.pw_gid)
            fresh_directory(CONTROL/'jobs',0o750,gid=user.pw_gid)
            write_new(CONTROL/'admission',b'HOLD\n',0o640,user.pw_gid)
            fresh_state(user)
            write_new(UNIT_FILE,render(source),0o644)
            switch.command('/usr/bin/systemctl','daemon-reload')
            verify_config(source,user.pw_gid)
            result = rehearse(source,controller_source,prior,user)
        except BaseException:
            # Preserve any partial installation; rehearsal handles its own stop.
            raise
    if action in ('observe-hold','stop-hold'):
        require(hold.attest() == prior, 'LANE_INSTALL_LEGACY_CHANGED')
    return dict(audit='LIGHT_NATIVE_LANE_INSTALL',source=source,action=action,
        legacy_unchanged=True,credentials_installed=False,boot_enabled=False,**result)


def program(raw, source, accepted, action, retained_raw=None, retained_accepted=''):
    retained = action in ('complete-retained-hold','observe-retained-hold','stop-retained-hold','inspect-retained-hold')
    require(retained or action in ('install-hold','observe-hold','stop-hold'), 'LANE_INSTALL_ACTION')
    require(release.source.identifier(source,40) and release.source.identifier(accepted,64)
            and release.hashlib.sha256(raw).hexdigest() == accepted, 'LANE_INSTALL_PACKAGE_NOT_ACCEPTED')
    if retained:
        require(type(retained_raw) is bytes and release.source.identifier(retained_accepted,64)
                and release.hashlib.sha256(retained_raw).hexdigest() == retained_accepted
                and json.loads(retained_raw)['source'] == RETAINED_SOURCE,
                'LANE_INSTALL_RETAINED_PACKAGE_NOT_ACCEPTED')
    else:
        require(retained_raw is None and retained_accepted == '', 'LANE_INSTALL_UNEXPECTED_RETAINED')
    return '''import base64,hashlib,json,os,pathlib,sys,tempfile
try:
 raw=base64.b64decode(%r,validate=True)
 assert hashlib.sha256(raw).hexdigest()==%r
 obj=json.loads(raw)
 assert obj['version']==1 and obj['source']==%r and set(obj['helpers'])==set(%r)
 assert os.geteuid()==0
 retained_raw=base64.b64decode(%r,validate=True)
 candidate=obj
 if retained_raw:
  assert hashlib.sha256(retained_raw).hexdigest()==%r
  candidate=json.loads(retained_raw)
  assert candidate['version']==1 and candidate['source']==%r
 with tempfile.TemporaryDirectory(prefix='light-lane-install-',dir='/var/tmp') as temp:
  root=pathlib.Path(temp)
  (root/'ops').mkdir(mode=0o700)
  for name,data in obj['helpers'].items():
   fd=os.open(root/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
   with os.fdopen(fd,'w') as stream:stream.write(data)
  sys.path.insert(0,str(root))
  from ops.light_native_lane_install import operation
  print(json.dumps(operation(candidate['runtime'],candidate['source'],%r,controller_source=obj['source']),sort_keys=True))
except BaseException as error:
 allowed={'LANE_INSTALL_LEGACY_FINGERPRINT_CHANGED','LANE_INSTALL_PRIOR_CHANGED','LANE_INSTALL_REENTRY_LEDGER',
  'LANE_INSTALL_REENTRY_ALREADY_STARTED','LANE_INSTALL_REENTRY_STATE',
  'LANE_INSTALL_PARENT','LANE_INSTALL_UNIT_CHANGED','LANE_INSTALL_ADMISSION_CHANGED',
  'LANE_INSTALL_LOADED_CONFIG','LANE_INSTALL_EXECUTION','LANE_INSTALL_UNIT_FIELDS',
  'LANE_INSTALL_ENVIRONMENT_FILES','LANE_INSTALL_PROCESS_REMAINS',
  'LANE_INSTALL_CGROUP','LANE_INSTALL_CGROUP_POPULATED',
  'RELEASE_INVENTORY_DRIFT','RELEASE_OWNER_OR_LINK','RELEASE_DIRECTORY_MODE',
  'RELEASE_FILE_DRIFT','CURRENT_MAIN_CHANGED','UNIT_DRIFT','POST_CHECK_DRIFT',
  'LOGIN_QUEUE_NOT_EMPTY','LOGIN_CONNECT_FAILED','LOGIN_AUTH_FAILED',
  'LANE_INSTALL_NOT_RUNNING','LANE_INSTALL_PROCESS_IDENTITY',
  'LANE_INSTALL_PROCESS_COMMAND','LANE_INSTALL_PROCESS_ENVIRONMENT',
  'LANE_INSTALL_STATE_CHANGED','LANE_INSTALL_HOLD_NOT_OBSERVED',
  'LANE_INSTALL_LEGACY_CHANGED'}
 code=str(error) if str(error) in allowed else 'UNCLASSIFIED'
 print(json.dumps({'audit':'LIGHT_NATIVE_LANE_INSTALL_REFUSED','code':code}))
 sys.exit(2)
''' % (base64.b64encode(raw).decode(),accepted,source,release.HELPERS,
       base64.b64encode(retained_raw or b'').decode(),retained_accepted,RETAINED_SOURCE,
       action.replace('-retained',''))


if __name__ == '__main__':
    require(len(sys.argv) in (4,5),'LANE_INSTALL_ARGUMENTS')
    source,accepted,action = sys.argv[1:4]
    retained = release.package(Path.cwd(),RETAINED_SOURCE) if '-retained-' in action else None
    print(program(release.package(Path.cwd(),source),source,accepted,action,retained,
                  sys.argv[4] if len(sys.argv)==5 else ''))
