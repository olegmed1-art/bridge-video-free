"""Upgrade the reviewed tunnel account to a fixed, leased route reader."""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

ROOT = Path('/var/lib/bridge-autopilot-tunnel')
DROP = Path('/etc/ssh/sshd_config.d/40-bridge-autopilot-tunnel.conf')
PROGRAM = Path('/usr/local/lib/bridge-autopilot-route/route.py')
COMMAND = '/usr/bin/python3 -I ' + str(PROGRAM)
PROGRAM_SHA256 = 'ffa71d71ff2a441ad7b5282aae1e41e0cf852b91f4b6f161629fd3f60b7cb9e1'
CA_SHA256 = '1ee37914846a8f85aff90523937ed6dde63517f239782e782190eb5038799212'
KEY_FP = 'SHA256:pJgl7Mw6K+v4IBPCaH46uaNCKh3uStnRBxcV5m85oqI'
USER = 'autopilot-db-tunnel'
OLD_CONFIG = '''# Managed by bridge-autopilot-pg-tunnel-v1
Match User autopilot-db-tunnel
    AuthorizedKeysFile /var/lib/bridge-autopilot-tunnel/authorized_keys
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    AllowTcpForwarding local
    PermitOpen 127.0.0.1:55432
    PermitListen none
    AllowStreamLocalForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTTY no
    PermitUserRC no
    ForceCommand /usr/bin/false
Match all
'''
NEW_CONFIG = OLD_CONFIG.replace('ForceCommand /usr/bin/false', 'ForceCommand ' + COMMAND)
OLD_OPTIONS = 'restrict,port-forwarding,permitopen="127.0.0.1:55432"'
NEW_OPTIONS = OLD_OPTIONS + ',command="' + COMMAND + '"'


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def directory(path):
    assert not path.is_symlink() and path.is_dir()
    info = path.stat()
    assert info.st_uid == 0 and not info.st_mode & 0o022


def read(path, mode=0o644):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r') as file:
        info = os.fstat(file.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid == 0
        assert stat.S_IMODE(info.st_mode) == mode and info.st_size < 65536
        return file.read(65536)


def create(path, content):
    assert not path.is_symlink()
    if path.exists():
        assert read(path) == content, 'managed_file_drift'
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'w') as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    path.chmod(0o644)


def replace(path, old, new):
    assert read(path) == old, 'replace_precondition_failed'
    temporary = path.with_name(path.name + '.bridge-route-tmp')
    assert not temporary.exists() and not temporary.is_symlink()
    create(temporary, new)
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def effective(user):
    value = run('/usr/sbin/sshd', '-T', '-C', 'user=' + user + ',host=github-runner,addr=192.0.2.1,laddr=92.5.47.149,lport=22')
    return dict(line.split(' ', 1) for line in value.splitlines())


def main():
    assert os.geteuid() == 0 and run('hostname') == 'autopilot-lite-vnic'
    assert hashlib.sha256(ROUTE_PROGRAM.encode()).hexdigest() == PROGRAM_SHA256
    directory(ROOT)
    assert read(ROOT / 'managed_by') == 'bridge-autopilot-pg-tunnel-v1\n'
    account = pwd.getpwnam(USER)
    assert account.pw_dir == str(ROOT) and account.pw_shell in {'/usr/sbin/nologin','/bin/sh'}
    assert run('id', '-Gn', USER) == USER and run('passwd', '-S', USER).split()[1] == 'L'
    ubuntu_keys = Path('/home/ubuntu/.ssh/authorized_keys').read_bytes()
    ubuntu_before = effective('ubuntu')
    old_config = read(DROP)
    assert old_config in {OLD_CONFIG, NEW_CONFIG}, 'unexpected_tunnel_config'
    old_key = read(ROOT / 'authorized_keys')
    options, raw = old_key.strip().split(' ssh-ed25519 ', 1)
    key, comment = raw.split(' ', 1)
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(key, validate=True)).digest()).decode().rstrip('=')
    assert fingerprint == KEY_FP and comment == 'bridge-autopilot-pg-tunnel'
    assert options in {OLD_OPTIONS, NEW_OPTIONS}
    assert account.pw_shell != '/bin/sh' or options == NEW_OPTIONS, 'unconfined_shell_drift'
    new_key = NEW_OPTIONS + ' ssh-ed25519 ' + key + ' ' + comment + '\n'
    for parent in [Path('/usr'),Path('/usr/local'),Path('/usr/local/lib')]:
        directory(parent)
    if not PROGRAM.parent.exists():
        PROGRAM.parent.mkdir(mode=0o755)
        PROGRAM.parent.chmod(0o755)
    directory(PROGRAM.parent)
    create(PROGRAM, ROUTE_PROGRAM)
    ca = read(Path('/etc/bridge-autopilot-postgres/tls/ca.crt'))
    assert hashlib.sha256(ca.encode()).hexdigest() == CA_SHA256
    create(ROOT / 'ca.crt', ca)
    create(ROOT / 'route.lock', '')  # Permanent inode: never atomically replace this file.
    lock = (ROOT / 'route.lock').stat()
    create(ROOT / 'lock-identity.json', json.dumps({'device': lock.st_dev, 'inode': lock.st_ino}) + '\n')
    if not (ROOT / 'route.json').exists():
        create(ROOT / 'route.json', json.dumps({'version':1,'backend':'neon','database':'autopilot','epoch':0}) + '\n')
    route = json.loads(read(ROOT / 'route.json'))
    assert set(route) == {'version','backend','database','epoch'} and route['version'] == 1
    assert route['backend'] == 'neon' and route['database'] == 'autopilot' and route['epoch'] == 0, 'pre_cutover_only'
    run('/usr/sbin/sshd', '-t')
    if old_config == NEW_CONFIG and old_key == new_key and account.pw_shell == '/bin/sh':
        expected = {'forcecommand':COMMAND,'allowtcpforwarding':'local','permitopen':'127.0.0.1:55432',
                    'permitlisten':'none','allowstreamlocalforwarding':'no','allowagentforwarding':'no',
                    'permittty':'no','permituserrc':'no','permituserenvironment':'no','x11forwarding':'no',
                    'passwordauthentication':'no','kbdinteractiveauthentication':'no',
                    'authenticationmethods':'publickey','authorizedkeysfile':str(ROOT / 'authorized_keys')}
        state = effective(USER)
        assert all(state.get(k) == v for k,v in expected.items())
        assert run('systemctl', 'is-active', 'ssh.service') == 'active'
        assert effective('ubuntu') == ubuntu_before and Path('/home/ubuntu/.ssh/authorized_keys').read_bytes() == ubuntu_keys
        print(json.dumps({'routing_lease':'ALREADY_INSTALLED','backend':'neon','epoch':0,'database_writes':False,'ubuntu_access_unchanged':True}))
        return
    try:
        # Key-level fixed command must precede enabling an executable account shell.
        replace(ROOT / 'authorized_keys', old_key, new_key)
        replace(DROP, old_config, NEW_CONFIG)
        run('usermod', '--shell', '/bin/sh', USER)
        run('/usr/sbin/sshd', '-t')
        state = effective(USER)
        expected = {'forcecommand':COMMAND,'allowtcpforwarding':'local','permitopen':'127.0.0.1:55432',
                    'permitlisten':'none','allowstreamlocalforwarding':'no','allowagentforwarding':'no',
                    'permittty':'no','permituserrc':'no','permituserenvironment':'no','x11forwarding':'no',
                    'passwordauthentication':'no','kbdinteractiveauthentication':'no',
                    'authenticationmethods':'publickey','authorizedkeysfile':str(ROOT / 'authorized_keys')}
        assert all(state.get(k) == v for k,v in expected.items())
        assert effective('ubuntu') == ubuntu_before and Path('/home/ubuntu/.ssh/authorized_keys').read_bytes() == ubuntu_keys
        run('systemctl', 'reload', 'ssh.service')
        assert run('systemctl', 'is-active', 'ssh.service') == 'active'
    except BaseException:
        run('usermod', '--shell', '/usr/sbin/nologin', USER)
        if read(ROOT / 'authorized_keys') == new_key:
            replace(ROOT / 'authorized_keys', new_key, old_key)
        if read(DROP) == NEW_CONFIG:
            replace(DROP, NEW_CONFIG, old_config)
        run('usermod', '--shell', account.pw_shell, USER)
        run('/usr/sbin/sshd', '-t')
        run('systemctl', 'reload', 'ssh.service')
        raise
    print(json.dumps({'routing_lease':'INSTALLED','backend':'neon','epoch':0,'database_writes':False,'ubuntu_access_unchanged':True}))


if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] == 'bundle':
        program = Path(__file__).with_name('oracle_light_route_lease.py').read_text()
        assert hashlib.sha256(program.encode()).hexdigest() == PROGRAM_SHA256
        print('ROUTE_PROGRAM = ' + repr(program))
        print(Path(__file__).read_text())
    else:
        main()
