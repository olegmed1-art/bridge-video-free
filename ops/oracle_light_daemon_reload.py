"""One exact, no-restart systemd reload on the installed Light HOLD host."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

UNIT = 'school-autopilot-production-light.service'
BASE = Path('/etc/systemd/system') / UNIT
DROP = Path('/etc/systemd/system/school-autopilot-production-light.service.d/40-reviewed-runtime-hold.conf')
RELEASE = '/opt/bridge-school/school-autopilot-production-light/releases/3244f4d4b17ce99e58c342f01e4715436a09622b'
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
DROP_SHA = 'bc18cbfbd932e3dd52be6c3070a2889a162c35469a507d6a25cdfc26b331a9c6'


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def pinned_file(path, digest):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        meta = os.fstat(stream.fileno())
        require(stat.S_ISREG(meta.st_mode) and meta.st_uid == 0
                and stat.S_IMODE(meta.st_mode) == 0o644 and meta.st_size < 65536,
                'UNIT_FILE_UNTRUSTED')
        data = stream.read(65536)
    require(hashlib.sha256(data).hexdigest() == digest, 'UNIT_FILE_DRIFT')
    return data


def show():
    keys = ('ActiveState', 'SubState', 'MainPID', 'InvocationID', 'WorkingDirectory',
            'Environment', 'User', 'Group', 'FragmentPath', 'DropInPaths',
            'ExecStart', 'NeedDaemonReload')
    output = subprocess.run(['systemctl', 'show', UNIT,
                             *['--property=' + key for key in keys]],
                            check=True, capture_output=True, text=True, timeout=15).stdout
    return dict(line.split('=', 1) for line in output.splitlines())


def validate(state):
    require(state['ActiveState'] == 'active' and state['SubState'] == 'running'
            and int(state['MainPID']) > 0
            and re.fullmatch('[a-f0-9]{32}', state['InvocationID']),
            'SERVICE_NOT_STABLE')
    require(state['WorkingDirectory'] == RELEASE
            and state['User'] == state['Group'] == 'school-autopilot'
            and state['FragmentPath'] == str(BASE)
            and state['DropInPaths'] == str(DROP)
            and 'AUTOPILOT_ADMISSION_MODE=HOLD' in state['Environment'].split()
            and ' -m oracle_autopilot.worker_v17 ;' in state['ExecStart'],
            'SERVICE_DRIFT')


def main():
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic',
            'HOST_IDENTITY')
    base = pinned_file(BASE, BASE_SHA)
    drop = pinned_file(DROP, DROP_SHA)
    before = show()
    validate(before)
    require(before['NeedDaemonReload'] == 'yes', 'RELOAD_NOT_REQUIRED')
    require(len(sys.argv) == 2 and re.fullmatch('[a-f0-9]{32}', sys.argv[1])
            and before['InvocationID'] == sys.argv[1],
            'INVOCATION_CHANGED')
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
    after = show()
    validate(after)
    require(after['NeedDaemonReload'] == 'no'
            and all(after[k] == before[k] for k in before if k != 'NeedDaemonReload')
            and pinned_file(BASE, BASE_SHA) == base
            and pinned_file(DROP, DROP_SHA) == drop,
            'POST_RELOAD_DRIFT')
    print(json.dumps({'reload': 'PASS', 'restart': False, 'admission': 'HOLD',
                      'pid': int(after['MainPID']), 'invocation_id': after['InvocationID'],
                      'release': RELEASE, 'need_daemon_reload': 'no'}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'reload': 'NOT_CONFIRMED', 'error_type': type(exc).__name__,
                          'error': str(exc)[:100]}))
        raise SystemExit(2) from None
