"""Install only the fixed, unprivileged local disk health timer on Light."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

MONITOR = '''#!/usr/bin/python3
import json
import os
from pathlib import Path
from datetime import datetime, timezone

def sample(path):
    s = os.statvfs(path)
    return {'path': path, 'free_bytes': s.f_bavail * s.f_frsize,
            'used_percent': round(100 * (s.f_blocks-s.f_bfree) / s.f_blocks, 1),
            'inode_used_percent': round(100 * (s.f_files-s.f_ffree) / s.f_files, 1)}

root = sample('/')
data = sample('/srv/autopilot-data') if os.path.ismount('/srv/autopilot-data') else None
disks = [root] + ([data] if data else [])
warning = any(d['used_percent'] >= 75 or d['inode_used_percent'] >= 75 for d in disks)
result = {'checked_at': datetime.now(timezone.utc).isoformat(),
          'status': 'WARNING' if warning else 'OK', 'disks': disks,
          'data_mount': 'PRESENT' if data else 'NOT_CONFIGURED',
          'backup_verification': 'NOT_CONFIGURED',
          'scope': 'local_capacity_only_no_external_alert_delivery'}
payload = json.dumps(result, sort_keys=True)
target = Path('/var/lib/bridge-light-health/status.json')
tmp = target.with_suffix('.tmp')
tmp.write_text(payload + '\\n')
tmp.replace(target)
print(payload)
'''

SERVICE = '''[Unit]
Description=Bridge Light local disk and inode capacity check
[Service]
Type=oneshot
DynamicUser=yes
StateDirectory=bridge-light-health
StateDirectoryMode=0755
ExecStart=/usr/local/lib/bridge-light-health/check.py
TimeoutStartSec=30
MemoryMax=64M
CPUQuota=10%
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_UNIX
'''

TIMER = '''[Unit]
Description=Check Bridge Light local disk capacity every 15 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
RandomizedDelaySec=30
Persistent=yes
[Install]
WantedBy=timers.target
'''

FILES = {
    '/usr/local/lib/bridge-light-health/check.py': (MONITOR, 0o755),
    '/etc/systemd/system/bridge-light-health.service': (SERVICE, 0o644),
    '/etc/systemd/system/bridge-light-health.timer': (TIMER, 0o644),
}

def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=45).stdout

def main():
    assert os.geteuid() == 0
    assert run('/usr/bin/hostname').strip() == 'autopilot-lite-vnic'
    # Refuse to overwrite pre-existing divergent files; reruns of this version are idempotent.
    for name, (content, _) in FILES.items():
        p = Path(name)
        assert not any(x.is_symlink() for x in [p, *p.parents])
        if p.exists():
            assert p.is_file() and p.read_text() == content, 'preexisting divergent file'
    for name, (content, mode) in FILES.items():
        p = Path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', dir=p.parent, delete=False) as f:
            f.write(content)
            tmp = Path(f.name)
        tmp.chmod(mode)
        tmp.replace(p)
    run('/usr/bin/systemd-analyze', 'verify', '/etc/systemd/system/bridge-light-health.service',
        '/etc/systemd/system/bridge-light-health.timer')
    run('/usr/bin/systemctl', 'daemon-reload')
    run('/usr/bin/systemctl', 'enable', '--now', 'bridge-light-health.timer')
    run('/usr/bin/systemctl', 'start', 'bridge-light-health.service')
    assert run('/usr/bin/systemctl', 'is-enabled', 'bridge-light-health.timer').strip() == 'enabled'
    assert run('/usr/bin/systemctl', 'is-active', 'bridge-light-health.timer').strip() == 'active'
    state = json.loads(Path('/var/lib/bridge-light-health/status.json').read_text())
    print(json.dumps({'installation': 'PASS', 'timer': 'enabled_active', 'health': state}))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'installation': 'FAILED', 'error_type': type(exc).__name__}))
        sys.exit(2)
