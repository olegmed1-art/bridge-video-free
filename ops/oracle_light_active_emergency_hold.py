"""Stop only the pinned Light ACTIVE worker and restore its previous HOLD drop-in."""
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
RELEASE = '/opt/bridge-school/school-autopilot-production-light/releases/3244f4d4b17ce99e58c342f01e4715436a09622b'
BASE_SHA = 'e8d22d57089b9c43b1d65d78de7e022c2b2124357cf40e98728128d2980ee2b4'
HOLD = f'[Service]\nWorkingDirectory={RELEASE}\nEnvironment=AUTOPILOT_ADMISSION_MODE=HOLD\n'.encode()
ACTIVE = HOLD.replace(b'ADMISSION_MODE=HOLD', b'ADMISSION_MODE=ACTIVE')


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def show():
    keys = ('ActiveState', 'SubState', 'MainPID', 'InvocationID', 'WorkingDirectory',
            'User', 'Group', 'Environment', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
    result = subprocess.run(['systemctl', 'show', UNIT,
                             *['--property=' + key for key in keys]],
                            check=True, capture_output=True, text=True, timeout=15)
    return dict(line.split('=', 1) for line in result.stdout.splitlines())


def main():
    require(os.geteuid() == 0 and os.uname().nodename == 'autopilot-lite-vnic', 'HOST_IDENTITY')
    require(len(sys.argv) == 2 and re.fullmatch(r'[a-f0-9]{32}', sys.argv[1]), 'INPUT_INVALID')
    require(not BASE.is_symlink() and not DROP.is_symlink() and
            BASE.stat().st_uid == DROP.stat().st_uid == 0 and
            hashlib.sha256(BASE.read_bytes()).hexdigest() == BASE_SHA and
            DROP.read_bytes() == ACTIVE, 'UNIT_OR_ACTIVE_DRIFT')
    before = show()
    require(before['ActiveState'] == 'active' and before['SubState'] == 'running' and
            int(before['MainPID']) > 0 and before['InvocationID'] == sys.argv[1] and
            before['WorkingDirectory'] == RELEASE and before['User'] == before['Group'] == 'school-autopilot' and
            before['FragmentPath'] == str(BASE) and before['DropInPaths'] == str(DROP) and
            'AUTOPILOT_ADMISSION_MODE=ACTIVE' in before['Environment'].split() and
            before['NeedDaemonReload'] == 'no', 'LIVE_ACTIVE_DRIFT')
    subprocess.run(['systemctl', 'stop', UNIT], check=True, timeout=45)
    stopped = show()
    require(stopped['ActiveState'] == 'inactive' and int(stopped['MainPID']) == 0,
            'STOP_NOT_ATTESTED')
    temp = DROP.parent / '.40-reviewed-runtime-recover-hold.tmp'
    with open(temp, 'xb') as stream:
        stream.write(HOLD)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, 0o644)
    os.replace(temp, DROP)
    subprocess.run(['systemctl', 'daemon-reload'], check=True, timeout=30)
    after = show()
    require(after['ActiveState'] == 'inactive' and int(after['MainPID']) == 0 and
            after['NeedDaemonReload'] == 'no' and
            'AUTOPILOT_ADMISSION_MODE=HOLD' in after['Environment'].split() and
            DROP.read_bytes() == HOLD, 'HOLD_NOT_ATTESTED')
    print(json.dumps({'emergency_hold':'PASS','service':'inactive','main_pid':0,
                      'admission':'HOLD','dispatch_replayed':False}))


if __name__ == '__main__':
    main()
