"""Pinned, two-stage administrative stop and activation for the Light canary."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

UNIT = 'school-autopilot-production-light.service'
BASE = Path('/etc/systemd/system') / UNIT
DROP = Path('/etc/systemd/system/school-autopilot-production-light.service.d/40-reviewed-runtime-hold.conf')
ROUTE = Path('/var/lib/bridge-autopilot-tunnel')
RELEASE = '/opt/bridge-school/school-autopilot-production-light/releases/3244f4d4b17ce99e58c342f01e4715436a09622b'
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
DROP_SHA = 'bc18cbfbd932e3dd52be6c3070a2889a162c35469a507d6a25cdfc26b331a9c6'
HOLD = f'[Service]\nWorkingDirectory={RELEASE}\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'.encode()
ACTIVE = HOLD.replace(b'ADMISSION_MODE=HOLD', b'ADMISSION_MODE=ACTIVE')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def read_pinned(path, digest):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(info.st_uid == 0 and info.st_mode & 0o777 == 0o644 and info.st_size < 65536,
                'FILE_METADATA_DRIFT')
        data = stream.read()
    require(hashlib.sha256(data).hexdigest() == digest, 'FILE_DIGEST_DRIFT')
    return data


def show():
    keys = ('ActiveState', 'SubState', 'MainPID', 'InvocationID', 'WorkingDirectory',
            'User', 'Group', 'Environment', 'ExecStart', 'FragmentPath', 'DropInPaths',
            'NeedDaemonReload')
    raw = subprocess.run(['systemctl', 'show', UNIT, *['--property=' + key for key in keys]],
                         check=True, capture_output=True, text=True, timeout=15).stdout
    return dict(line.split('=', 1) for line in raw.splitlines())


def common(state, admission):
    require(state['WorkingDirectory'] == RELEASE and state['User'] == state['Group'] == 'school-autopilot'
            and state['FragmentPath'] == str(BASE) and state['DropInPaths'] == str(DROP)
            and ' -m oracle_autopilot.worker_v17 ;' in state['ExecStart']
            and f'AUTOPILOT_ADMISSION_MODE={admission}' in state['Environment'].split()
            and state['NeedDaemonReload'] == 'no', 'SERVICE_DRIFT')


def pinned_route():
    route = ROUTE / 'route.json'
    require(not route.is_symlink() and route.stat().st_uid == 0, 'ROUTE_METADATA_DRIFT')
    require(json.loads(route.read_text()) == {'version': 1, 'backend': 'neon',
            'database': 'autopilot', 'epoch': 0}, 'ROUTE_CHANGED')


def run(command):
    subprocess.run(['systemctl', command, UNIT], check=True, timeout=45)


def install_active():
    temp = DROP.parent / '.40-reviewed-runtime-active.tmp'
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(ACTIVE)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, DROP)
        os.chmod(DROP, 0o644)
        subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
    finally:
        temp.unlink(missing_ok=True)


def main():
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'HOST_IDENTITY')
    require(len(sys.argv) == 3 and sys.argv[1] in ('stop', 'activate')
            and re.fullmatch(r'[a-f0-9]{32}', sys.argv[2]), 'INPUT_INVALID')
    action, invocation = sys.argv[1:]
    lock = ROUTE / 'route.lock'
    with lock.open('rb') as stream:
        fcntl.flock(stream, fcntl.LOCK_SH | fcntl.LOCK_NB)
        pinned_route()
        require(read_pinned(BASE, BASE_SHA) and read_pinned(DROP, DROP_SHA) == HOLD,
                'UNIT_OR_HOLD_DRIFT')
        before = show()
        common(before, 'HOLD')
        if action == 'stop':
            require(before['ActiveState'] == 'active' and before['SubState'] == 'running'
                    and int(before['MainPID']) > 0 and before['InvocationID'] == invocation,
                    'INVOCATION_DRIFT')
            run('stop')
            after = show()
            common(after, 'HOLD')
            require(after['ActiveState'] == 'inactive' and int(after['MainPID']) == 0
                    and read_pinned(DROP, DROP_SHA) == HOLD, 'STOP_NOT_ATTESTED')
            pinned_route()
            print(json.dumps({'action':'stop','status':'PASS','main_pid':0,'admission':'HOLD'}))
            return
        require(before['ActiveState'] == 'inactive' and int(before['MainPID']) == 0
                and invocation == '0' * 32, 'STOP_BOUNDARY_NOT_ATTESTED')
        try:
            install_active()
            require(DROP.read_bytes() == ACTIVE, 'ACTIVE_DROP_DRIFT')
            run('start')
            after = show()
            common(after, 'ACTIVE')
            require(after['ActiveState'] == 'active' and after['SubState'] == 'running'
                    and int(after['MainPID']) > 0, 'START_NOT_ATTESTED')
            pinned_route()
            print(json.dumps({'action':'activate','status':'PASS','main_pid':int(after['MainPID']),
                              'invocation_id':after['InvocationID'],'admission':'ACTIVE'}))
        except BaseException:
            run('stop')
            temp = DROP.parent / '.40-reviewed-runtime-restore.tmp'
            with open(temp, 'xb') as output:
                output.write(HOLD)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temp, 0o644)
            os.replace(temp, DROP)
            subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
            raise


if __name__ == '__main__':
    main()
