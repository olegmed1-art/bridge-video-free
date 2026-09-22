"""Provision only the verified new data disk; refuse existing unknown filesystems."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid

DEVICE = Path('/dev/oracleoci/oraclevdb')
MOUNT = Path('/srv/autopilot-data')
STATE = Path('/var/lib/bridge-light-data-mount/provision.json')
UNIT = Path('/etc/systemd/system/srv-autopilot\\x2ddata.mount')
LABEL = 'autopilot-data'
SIZE = 50 * 1024 ** 3


def run(*args, timeout=60):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout).stdout.strip()


def no_symlinks(path):
    assert not any(p.is_symlink() for p in [path, *path.parents])


def disk_inventory():
    rows = json.loads(run('lsblk', '-J', '-b', '-p', '-o',
                         'PATH,SIZE,TYPE,FSTYPE,MOUNTPOINTS,UUID,SERIAL'))['blockdevices']
    real = str(DEVICE.resolve(strict=True))
    matches = [d for d in rows if d['path'] == real]
    assert len(matches) == 1, 'target must be a whole disk'
    disk = matches[0]
    assert disk['type'] == 'disk' and disk['size'] == SIZE
    assert not disk.get('children'), 'partitioned disk refused'
    assert disk.get('serial'), 'stable disk identity required'
    assert all(p in (None, str(MOUNT)) for p in disk['mountpoints'])
    assert stat.S_ISBLK(DEVICE.stat().st_mode)
    return disk


def unit_text(fs_uuid):
    return ('[Unit]\nDescription=Bridge autopilot dedicated data disk\n'
            '[Mount]\nWhat=/dev/disk/by-uuid/' + fs_uuid + '\n'
            'Where=/srv/autopilot-data\nType=ext4\nOptions=defaults,nodev,nosuid\n'
            'TimeoutSec=60\n[Install]\nWantedBy=multi-user.target\n')


def main():
    assert os.geteuid() == 0
    assert run('hostname') == 'autopilot-lite-vnic'
    for path in (MOUNT, STATE, UNIT):
        no_symlinks(path)
    disk = disk_inventory()
    assert not any(str(MOUNT) in line.split() for line in Path('/etc/fstab').read_text().splitlines()
                   if line.strip() and not line.lstrip().startswith('#')), 'existing fstab entry refused'
    if STATE.exists():
        state = json.loads(STATE.read_text())
        assert state['serial'] == disk['serial'] and state['size'] == SIZE
        assert state['device'] == str(DEVICE) and state['version'] == 1
        fs_uuid = str(uuid.UUID(state['uuid']))
        assert disk['fstype'] == 'ext4' and disk['uuid'] == fs_uuid, 'partial/unknown provisioning refused'
        assert run('blkid', '-s', 'LABEL', '-o', 'value', str(DEVICE)) == LABEL
    else:
        assert not UNIT.exists(), 'unknown mount unit refused'
        assert not MOUNT.exists() or (MOUNT.is_dir() and not any(MOUNT.iterdir()))
        assert not os.path.ismount(MOUNT)
        assert not disk['fstype'] and not disk['uuid']
        assert not any(disk['mountpoints'])
        signatures = json.loads(run('wipefs', '--no-act', '--json', str(DEVICE)))
        assert not signatures.get('signatures'), 'existing disk signature refused'
        fs_uuid = str(uuid.uuid4())
        state = dict(version=1, serial=disk['serial'], size=SIZE, device=str(DEVICE), uuid=fs_uuid)
        STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with STATE.open('x') as file:
            os.chmod(STATE, 0o600)
            json.dump(state, file)
            file.flush()
            os.fsync(file.fileno())
        # No force flag: mkfs independently refuses a mounted/in-use device.
        run('mkfs.ext4', '-q', '-L', LABEL, '-U', fs_uuid, '-m', '1', str(DEVICE), timeout=180)
        run('udevadm', 'settle', '--timeout=30')
        disk = disk_inventory()
        assert disk['fstype'] == 'ext4' and disk['uuid'] == fs_uuid
    content = unit_text(fs_uuid)
    if UNIT.exists():
        assert UNIT.read_text() == content, 'divergent mount unit refused'
    else:
        with UNIT.open('x') as file:
            file.write(content)
        UNIT.chmod(0o644)
    MOUNT.mkdir(mode=0o755, parents=True, exist_ok=True)
    run('systemd-analyze', 'verify', str(UNIT))
    run('systemctl', 'daemon-reload')
    run('systemctl', 'enable', '--now', UNIT.name, timeout=90)
    assert os.path.ismount(MOUNT)
    mounted = json.loads(run('findmnt', '-J', '-M', str(MOUNT), '-o', 'TARGET,UUID,FSTYPE'))['filesystems']
    assert len(mounted) == 1 and mounted[0]['uuid'] == fs_uuid and mounted[0]['fstype'] == 'ext4'
    assert run('systemctl', 'is-enabled', UNIT.name) == 'enabled'
    print(json.dumps({'data_mount': 'VERIFIED', 'path': str(MOUNT), 'size_gib': 50,
                      'persistent_mount': True, 'postgresql_installed_by_this_job': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'data_mount': 'NOT_CONFIRMED', 'error_type': type(exc).__name__}))
        sys.exit(2)
