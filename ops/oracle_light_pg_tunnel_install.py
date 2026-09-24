"""Create a PostgreSQL-only SSH account; leave Ubuntu access unchanged."""
import base64
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import tempfile

USER = 'autopilot-db-tunnel'
HOME = Path('/var/lib/bridge-autopilot-tunnel')
DROP = Path('/etc/ssh/sshd_config.d/40-bridge-autopilot-tunnel.conf')
FINGERPRINT = 'SHA256:pJgl7Mw6K+v4IBPCaH46uaNCKh3uStnRBxcV5m85oqI'
CONFIG = '''# Managed by bridge-autopilot-pg-tunnel-v1
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


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def public_key(text):
    found = []
    for line in text.splitlines():
        fields = line.split()
        if 'ssh-ed25519' not in fields:
            continue
        index = fields.index('ssh-ed25519')
        raw = fields[index + 1]
        digest = base64.b64encode(hashlib.sha256(base64.b64decode(raw, validate=True)).digest()).decode().rstrip('=')
        if 'SHA256:' + digest == FINGERPRINT:
            assert fields[:index] == ['restrict'], 'existing_automation_options_changed'
            found.append('ssh-ed25519 ' + raw)
    assert len(found) == 1, 'automation_key_not_unique'
    return found[0]


def owned_file(path, content, mode):
    assert not path.is_symlink(), 'symlink_rejected'
    if path.exists():
        info = path.stat()
        assert stat.S_ISREG(info.st_mode) and info.st_uid == 0
        assert stat.S_IMODE(info.st_mode) == mode and path.read_text() == content, 'owned_file_drift'
        return False
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'w') as file:
        file.write(content)
        file.flush()
        os.fsync(file.fileno())
    path.chmod(mode)
    return True


def effective(user):
    text = run('/usr/sbin/sshd', '-T', '-C', 'user=' + user + ',host=github-runner,addr=192.0.2.1,laddr=92.5.47.149,lport=22')
    return dict(line.split(' ', 1) for line in text.splitlines())


def validate_effective(settings):
    expected = {
        'authorizedkeysfile': str(HOME / 'authorized_keys'),
        'authenticationmethods': 'publickey', 'passwordauthentication': 'no',
        'kbdinteractiveauthentication': 'no', 'allowtcpforwarding': 'local',
        'permitopen': '127.0.0.1:55432', 'permitlisten': 'none',
        'allowstreamlocalforwarding': 'no', 'allowagentforwarding': 'no',
        'x11forwarding': 'no', 'permittty': 'no', 'permituserrc': 'no',
        'forcecommand': '/usr/bin/false', 'disableforwarding': 'no',
    }
    assert all(settings.get(k) == v for k, v in expected.items()), 'effective_tunnel_policy_mismatch'


def main():
    assert os.geteuid() == 0 and run('hostname') == 'autopilot-lite-vnic'
    assert run('systemctl', 'is-active', 'ssh.service') == 'active'
    assert run('systemctl', 'is-active', 'bridge-autopilot-postgres.service') == 'active'
    source = Path('/home/ubuntu/.ssh/authorized_keys')
    original = source.read_bytes()
    key = public_key(original.decode())
    ubuntu_before = effective('ubuntu')
    run('/usr/sbin/sshd', '-t')
    assert not HOME.is_symlink() and not DROP.is_symlink()
    if HOME.exists():
        assert HOME.is_dir() and HOME.stat().st_uid == 0 and stat.S_IMODE(HOME.stat().st_mode) == 0o755
        assert (HOME / 'managed_by').read_text() == 'bridge-autopilot-pg-tunnel-v1\n'
    else:
        HOME.mkdir(mode=0o755)
        HOME.chmod(0o755)
        owned_file(HOME / 'managed_by', 'bridge-autopilot-pg-tunnel-v1\n', 0o644)
    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        run('useradd', '--system', '--user-group', '--no-create-home', '--home-dir', str(HOME), '--shell', '/usr/sbin/nologin', USER)
        account = pwd.getpwnam(USER)
    assert account.pw_dir == str(HOME) and account.pw_shell == '/usr/sbin/nologin' and account.pw_uid != 0
    assert run('id', '-Gn', USER) == USER, 'unexpected_tunnel_groups'
    assert run('passwd', '-S', USER).split()[1] == 'L', 'password_not_locked'
    owned_file(HOME / 'authorized_keys', 'restrict,port-forwarding,permitopen="127.0.0.1:55432" ' + key + ' bridge-autopilot-pg-tunnel\n', 0o644)
    # Root-owned home and key keep the tunnel account from changing authorization.
    installed = False
    try:
        installed = owned_file(DROP, CONFIG, 0o644)
        run('/usr/sbin/sshd', '-t')
        validate_effective(effective(USER))
        assert effective('ubuntu') == ubuntu_before, 'ubuntu_policy_changed'
        assert source.read_bytes() == original, 'ubuntu_keys_changed'
        run('systemctl', 'reload', 'ssh.service')
        assert run('systemctl', 'is-active', 'ssh.service') == 'active'
    except BaseException:
        if installed:
            DROP.unlink()
            run('/usr/sbin/sshd', '-t')
            run('systemctl', 'reload', 'ssh.service')
        raise
    print(json.dumps({'pg_tunnel_account': 'CONFIGURED', 'destination': '127.0.0.1:55432',
                      'ubuntu_access_unchanged': True, 'postgresql_public_port': False,
                      'database_roles_changed': False, 'credential_isolation': False}))


if __name__ == '__main__':
    main()
