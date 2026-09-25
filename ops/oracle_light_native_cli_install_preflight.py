"""Read-only Light HOLD inventory for choosing a native Codex CLI profile.

Never inspect session contents, environment files, npm configuration or tokens.
This script does not install software or alter the service or admission mode.
"""
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

UNIT = 'school-autopilot-production-light.service'
PROFILES = (
    ('ubuntu', Path('/home/ubuntu'), Path('/home/ubuntu/.codex'),
     Path('/home/ubuntu/.local/share/slavik-codex/node_modules/.bin')),
    ('school-autopilot', Path('/opt/bridge-school/school-autopilot/runtime'),
     Path('/opt/bridge-school/school-autopilot/runtime/codex-home'),
     Path('/opt/bridge-school/school-autopilot/runtime-bin')),
)
RUNTIME_BINARIES = ('/usr/local/bin/node', '/usr/bin/node',
                    '/home/ubuntu/.nvm/versions/node/v22.23.2/bin/node')
NPM_BINARIES = ('/usr/local/bin/npm', '/usr/bin/npm',
                '/home/ubuntu/.nvm/versions/node/v22.23.2/bin/npm')


def held():
    if os.geteuid() != 0 or os.uname().nodename != 'autopilot-lite-vnic':
        raise ValueError('HOST_IDENTITY')
    output = subprocess.run(['systemctl', 'show', UNIT, '-pActiveState', '-pSubState',
                             '-pEnvironment'], check=True, capture_output=True, text=True,
                            timeout=15).stdout
    fields = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    admission = [item for item in fields.get('Environment', '').split()
                 if item.startswith('AUTOPILOT_ADMISSION_MODE=')]
    if fields.get('ActiveState') != 'active' or fields.get('SubState') != 'running' or admission != [
            'AUTOPILOT_ADMISSION_MODE=HOLD']:
        raise ValueError('NOT_ACTIVE_HOLD')


def directory_status(path, uid):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return 'MISSING'
    if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022:
        return 'REVIEW_REQUIRED'
    return ('PROFILE_OWNED' if info.st_uid == uid else
            'ROOT_OWNED' if info.st_uid == 0 else 'REVIEW_REQUIRED')


def credential_directory_status(path, uid):
    try:
        info=path.lstat()
    except FileNotFoundError:
        return 'MISSING'
    if (stat.S_ISDIR(info.st_mode) and info.st_uid == uid
            and not stat.S_IMODE(info.st_mode) & 0o077):
        return 'PROFILE_PRIVATE'
    return 'REVIEW_REQUIRED'


def binary_candidate(paths, uid):
    for name in paths:
        path = Path(name)
        try:
            info = path.stat()
        except (OSError, ValueError):
            continue
        if (stat.S_ISREG(info.st_mode) and info.st_uid in (0,uid)
                and info.st_mode & stat.S_IXOTH and not info.st_mode & 0o022):
            return path
    return None


def profile(name, home, codex_home, install_parent):
    user = pwd.getpwnam(name)
    uid = user.pw_uid
    candidates = (RUNTIME_BINARIES if name == 'school-autopilot' else
                  (RUNTIME_BINARIES[-1], *RUNTIME_BINARIES[:-1]))
    npm = (NPM_BINARIES if name == 'school-autopilot' else
           (NPM_BINARIES[-1], *NPM_BINARIES[:-1]))
    return {'home':directory_status(home,uid),
            'credential_directory':credential_directory_status(codex_home,uid),
            'install_parent':directory_status(install_parent,uid),
            'node':'PRESENT_UNVERIFIED' if binary_candidate(candidates,uid) else 'ABSENT',
            'npm': 'PRESENT_UNVERIFIED' if binary_candidate(npm,uid) else 'ABSENT'}


def main():
    held()
    result={name:profile(name,home,codex_home,install_parent)
            for name,home,codex_home,install_parent in PROFILES}
    print(json.dumps({'audit':'NATIVE_CLI_INSTALL_PREFLIGHT','admission':'HOLD',
                      'profiles':result,'installation_action':'NONE'},sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'audit':'BLOCKED','code':'PREFLIGHT_FAILED'}))
        sys.exit(2)
