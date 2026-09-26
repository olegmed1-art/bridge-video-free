"""PID1-owned lifetime for the fixed read-only maintenance transport."""
import base64
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time
import uuid

UNIT = r'bridge-native-ro-[0-9a-f]{12}-[0-9]{1,20}-[0-9]{1,6}-[0-9a-f]{16}\.service'
PROPERTIES = {
    'Type': 'exec', 'ExitType': 'main', 'KillMode': 'control-group',
    'SendSIGKILL': 'yes', 'Restart': 'no', 'PrivateTmp': 'yes',
    'ProtectControlGroups': 'yes', 'NoNewPrivileges': 'yes',
    'LimitCORE': '0', 'TimeoutStopUSec': '2s',
}


def check(value, code):
    if not value:
        raise RuntimeError(code)


def ctl(*arguments):
    return subprocess.run(['/usr/bin/systemctl', *arguments], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin'}, timeout=5)


def show(unit):
    check(re.fullmatch(UNIT, unit), 'UNIT_INVALID')
    keys = (*PROPERTIES, 'RuntimeMaxUSec', 'LoadState', 'ActiveState', 'Result',
            'InvocationID', 'MainPID', 'ControlGroup', 'ExecMainCode', 'ExecMainStatus')
    result = ctl('show', unit, '--property=' + ','.join(keys))
    check(result.returncode == 0 and len(result.stdout) < 16384, 'UNIT_QUERY_FAILED')
    return dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)


def new_unit(source, run):
    check(re.fullmatch('[0-9a-f]{40}', source or '') and
          re.fullmatch('[0-9]{1,20}-[0-9]{1,6}', run or ''), 'RUN_IDENTITY_INVALID')
    unit = 'bridge-native-ro-' + source[:12] + '-' + run + '-' + uuid.uuid4().hex[:16] + '.service'
    # systemctl show unknown units may return nonzero; explicit LoadState required.
    result = ctl('show', unit, '--property=LoadState', '--value')
    check(result.stdout.strip() == b'not-found', 'UNIT_ALREADY_EXISTS')
    return unit


def command(unit, code, seconds):
    check(re.fullmatch(UNIT, unit) and seconds in (3, 100), 'UNIT_COMMAND_INVALID')
    return ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--unit=' + unit,
            '--service-type=exec', '--expand-environment=no',
            '--property=ExitType=main', '--property=KillMode=control-group',
            '--property=SendSIGKILL=yes', '--property=TimeoutStopSec=2s',
            '--property=RuntimeMaxSec=' + str(seconds) + 's', '--property=Restart=no',
            '--property=PrivateTmp=yes', '--property=ProtectControlGroups=yes',
            '--property=NoNewPrivileges=yes', '--property=LimitCORE=0',
            '/usr/bin/python3', '-I', '-B', '-S', '-c', code]


def identity(unit, seconds):
    state = show(unit)
    check(all(state.get(key) == value for key, value in PROPERTIES.items()), 'UNIT_PROPERTIES_DRIFT')
    check(state.get('RuntimeMaxUSec') == ('3s' if seconds == 3 else '1min 40s'), 'RUNTIME_LIMIT_DRIFT')
    check(state.get('ActiveState') == 'active' and
          re.fullmatch('[0-9a-f]{32}', state.get('InvocationID', '')) and
          int(state.get('MainPID', '0')) > 0 and
          state.get('ControlGroup') == '/system.slice/' + unit, 'UNIT_IDENTITY_DRIFT')
    group = Path('/sys/fs/cgroup' + state['ControlGroup'])
    check(group.is_dir() and 'populated 1' in (group / 'cgroup.events').read_text(), 'CGROUP_NOT_LIVE')
    return state, group, group.stat().st_ino


def assert_self(unit):
    state, _, _ = identity(unit, 100)
    check(int(state['MainPID']) == os.getpid() and
          Path('/proc/self/cgroup').read_text().strip() == '0::' + state['ControlGroup'], 'UNIT_SELF_MISMATCH')


def empty(group, inode):
    try:
        check(group.stat().st_ino == inode, 'CGROUP_REPLACED')
        return 'populated 0' in (group / 'cgroup.events').read_text()
    except FileNotFoundError:
        # This exact group was previously observed live with a unique unit name.
        check(not group.exists(), 'CGROUP_EVIDENCE_MISSING')
        return True


def cleanup(unit):
    # Only our unguessable name; never touch an existing application service.
    ctl('stop', unit)
    ctl('reset-failed', unit)


def loader(encoded):
    return ('import base64,types\n'
            "lifetime=types.ModuleType('native_lifetime')\n"
            "exec(compile(base64.b64decode(" + repr(encoded) + "),'native_lifetime','exec'),lifetime.__dict__)\n")


def managed(code, encoded_self, source, run):
    check(os.getuid() == 0, 'ROOT_REQUIRED')
    unit = new_unit(source, run)
    inner = loader(encoded_self) + 'lifetime.assert_self(' + repr(unit) + ')\n' + code
    try:
        # PID1 owns the transient service even if this wrapper/SSH client dies.
        result = subprocess.run(command(unit, inner, 100), timeout=108,
                                env={'PATH': '/usr/bin:/bin'}, close_fds=True)
        return result.returncode
    finally:
        cleanup(unit)


PROBE = '''
import json,os,signal,sys,time
reader,writer=os.pipe()
child=os.fork()
if child == 0:
 os.close(reader)
 os.setsid()
 signal.signal(signal.SIGTERM,signal.SIG_IGN)
 os.write(writer,b'R')
 os.close(writer)
 while True: time.sleep(1)
os.close(writer)
assert os.read(reader,1)==b'R'
os.close(reader)
print(json.dumps({'ready':True,'main':os.getpid(),'child':child}),flush=True)
signal.signal(signal.SIGTERM,signal.SIG_IGN)
while True: time.sleep(1)
'''


def probe(source, run, kind):
    check(kind in ('main-kill', 'runtime-max', 'launcher-kill'), 'PROBE_INVALID')
    unit = new_unit(source, run)
    process = None
    try:
        process = subprocess.Popen(command(unit, PROBE, 3), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   env={'PATH': '/usr/bin:/bin'}, close_fds=True)
        check(select.select([process.stdout], [], [], 2)[0], 'PROBE_READY_TIMEOUT')
        # Fixed trusted probe writes exactly one bounded line.
        record = json.loads(process.stdout.readline(1024))
        check(set(record) == {'ready', 'main', 'child'} and record['ready'] is True, 'PROBE_RECORD_INVALID')
        state, group, inode = identity(unit, 3)
        check(int(state['MainPID']) == record['main'], 'PROBE_MAIN_MISMATCH')
        child = record['child']
        check(Path(f'/proc/{child}/cgroup').read_text().strip() == '0::' + state['ControlGroup']
              and os.getsid(child) == child, 'PROBE_DESCENDANT_NOT_ISOLATED')
        # Signals address this created unit's main process, not an unchecked PID.
        signal_name = 'SIGKILL' if kind == 'main-kill' else 'SIGSTOP'
        check(ctl('kill', '--kill-whom=main', '--signal=' + signal_name, unit).returncode == 0,
              'PROBE_SIGNAL_FAILED')
        if kind == 'launcher-kill':
            # Kill the systemd-run client; PID1 must still enforce the stopped
            # service's own runtime deadline and reap its cgroup descendants.
            process.kill()
        process.communicate(timeout=10)
        if kind == 'launcher-kill':
            check(process.returncode == -9, 'PROBE_LAUNCHER_NOT_SIGKILL')
        after = show(unit)
        check(process.returncode != 0 and after.get('InvocationID') == state['InvocationID']
              and after.get('Result') == ('signal' if kind == 'main-kill' else 'timeout')
              and after.get('ActiveState') == 'failed', 'PROBE_FAILURE_MISMATCH')
        check(empty(group, inode), 'PROBE_CGROUP_STILL_POPULATED')
        if kind == 'main-kill':
            check(after.get('ExecMainStatus') == '9', 'PROBE_MAIN_NOT_SIGKILL')
        print(json.dumps({'audit': 'NATIVE_LIFETIME_PASS', 'case': kind,
                          'cgroup_empty': True, 'source_sha': source}), flush=True)
    finally:
        cleanup(unit)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def probes(source, run):
    check(os.getuid() == 0 and Path('/run/systemd/system').is_dir()
          and Path('/sys/fs/cgroup/cgroup.controllers').exists(), 'SYSTEMD_CGROUP2_REQUIRED')
    for kind in ('main-kill', 'runtime-max', 'launcher-kill'):
        probe(source, run, kind)


if __name__ == '__main__':
    try:
        check(sys.argv[1:] == ['--self-test'], 'SELF_TEST_ONLY')
        probes(os.environ.get('GITHUB_SHA'), os.environ.get('GITHUB_RUN_ID', '') + '-' +
               os.environ.get('GITHUB_RUN_ATTEMPT', ''))
    except BaseException:
        print(json.dumps({'audit': 'NATIVE_LIFETIME_REFUSED'}), flush=True)
        sys.exit(2)
