"""Bounded, sanitized CLI presence/auth probe on an active Light HOLD host."""
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys

UNIT = 'school-autopilot-production-light.service'
SERVICE_ROOT = Path('/opt/bridge-school/school-autopilot')
PROFILES = (
    ('ubuntu', Path('/home/ubuntu/.local/share/slavik-codex/node_modules/.bin/codex'),
     Path('/home/ubuntu'), Path('/home/ubuntu/.codex')),
    ('school-autopilot', SERVICE_ROOT / 'runtime-bin/codex',
     SERVICE_ROOT / 'runtime', SERVICE_ROOT / 'runtime/codex-home'),
)


def profile_status(name, binary, home, codex_home):
    if not binary.is_file() or not os.access(binary, os.X_OK):
        return 'CLI_ABSENT'
    user = pwd.getpwnam(name)
    path = ('/home/ubuntu/.nvm/versions/node/v22.23.2/bin:/usr/local/bin:/usr/bin:/bin'
            if name == 'ubuntu' else '/usr/local/bin:/usr/bin:/bin')

    def drop_privileges():
        os.setgroups([])
        os.setgid(user.pw_gid)
        os.setuid(user.pw_uid)

    try:
        result = subprocess.run(
            [str(binary), '-c', 'forced_login_method="chatgpt"', 'login', 'status'],
            cwd='/', env={'HOME': str(home), 'CODEX_HOME': str(codex_home),
                          'PATH': path, 'LANG': 'C.UTF-8'},
            preexec_fn=drop_privileges, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return 'CLI_STATUS_UNKNOWN'
    # Never print CLI output: it may contain account or credential material.
    return ('CLI_AUTH_READY' if result.returncode == 0 and
            'Logged in using ChatGPT' in (result.stdout + '\n' + result.stderr).splitlines()
            else 'CLI_AUTH_REQUIRED')


def main():
    if os.geteuid() != 0 or os.uname().nodename != 'autopilot-lite-vnic':
        raise ValueError('HOST_IDENTITY')
    output = subprocess.run(
        ['systemctl', 'show', UNIT, '-pActiveState', '-pSubState', '-pEnvironment'],
        check=True, capture_output=True, text=True, timeout=15).stdout
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    admission = [token for token in fields.get('Environment', '').split()
                 if token.startswith('AUTOPILOT_ADMISSION_MODE=')]
    if (fields.get('ActiveState') != 'active' or fields.get('SubState') != 'running'
            or admission != ['AUTOPILOT_ADMISSION_MODE=HOLD']):
        raise ValueError('NOT_ACTIVE_HOLD')
    states = {name: profile_status(name, binary, home, codex_home)
              for name, binary, home, codex_home in PROFILES}
    print(json.dumps({'audit': 'NATIVE_CLI_READINESS', 'admission': 'HOLD',
                      'profiles': states}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'audit': 'BLOCKED', 'code': 'PROBE_FAILED'}))
        sys.exit(2)
