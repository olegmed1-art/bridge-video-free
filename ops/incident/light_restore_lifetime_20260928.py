"""Incident-only fixed PID1 and pipe budgets; historical profiles are untouched.

This module supplies lifetime isolation and observation, never restore authority.
The authenticated runner and host must separately enforce their nonrenewing
900s/540s clocks and the independent 30-minute owner coordination window.
"""
import base64
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

RUNTIME_SECONDS = 600
WRAPPER_SECONDS = 608
HOST_SECONDS = 540
RUNNER_SECONDS = 900
RPC_SECONDS = 700
RUNTIME_TEXT = '10min'
UNIT = r'bridge-native-ro-[0-9a-f]{12}-[0-9]{1,20}-[0-9]{1,6}-[0-9a-f]{16}\.service'
PROPERTIES = {
    'Type': 'exec', 'ExitType': 'main', 'KillMode': 'control-group',
    'SendSIGKILL': 'yes', 'Restart': 'no', 'PrivateTmp': 'yes',
    'ProtectControlGroups': 'yes', 'NoNewPrivileges': 'yes',
    'LimitCORE': '0', 'TimeoutStopUSec': '2s',
}


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def ctl(*args):
    return subprocess.run(['/usr/bin/systemctl', *args], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin'}, timeout=5)


def show(unit):
    require(re.fullmatch(UNIT, unit), 'INCIDENT_UNIT')
    keys = (*PROPERTIES, 'RuntimeMaxUSec', 'LoadState', 'ActiveState', 'Result',
            'InvocationID', 'MainPID', 'ControlGroup', 'ExecMainCode', 'ExecMainStatus')
    result = ctl('show', unit, '--property=' + ','.join(keys))
    require(result.returncode == 0 and len(result.stdout) < 16384, 'INCIDENT_UNIT_QUERY')
    return dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)


def new_unit(source, run):
    require(re.fullmatch('[0-9a-f]{40}', source or '')
            and re.fullmatch('[1-9][0-9]{0,19}-[1-9][0-9]{0,5}', run or ''),
            'INCIDENT_UNIT_IDENTITY')
    unit = 'bridge-native-ro-' + source[:12] + '-' + run + '-' + uuid.uuid4().hex[:16] + '.service'
    require(ctl('show', unit, '--property=LoadState', '--value').stdout.strip() == b'not-found',
            'INCIDENT_UNIT_EXISTS')
    return unit


def command(unit, code):
    require(re.fullmatch(UNIT, unit) and type(code) is str and 0 < len(code.encode()) <= 120000,
            'INCIDENT_UNIT_COMMAND')
    return ['/usr/bin/systemd-run', '--quiet', '--wait', '--pipe', '--unit=' + unit,
            '--service-type=exec', '--expand-environment=no',
            '--property=ExitType=main', '--property=KillMode=control-group',
            '--property=SendSIGKILL=yes', '--property=TimeoutStopSec=2s',
            '--property=RuntimeMaxSec=600s', '--property=Restart=no',
            '--property=PrivateTmp=yes', '--property=ProtectControlGroups=yes',
            '--property=NoNewPrivileges=yes', '--property=LimitCORE=0',
            '/usr/bin/python3', '-I', '-B', '-S', '-c', code]


def identity(unit):
    state = show(unit)
    require(all(state.get(key) == value for key, value in PROPERTIES.items())
            and state.get('RuntimeMaxUSec') == RUNTIME_TEXT, 'INCIDENT_UNIT_PROFILE')
    require(state.get('ActiveState') == 'active'
            and re.fullmatch('[0-9a-f]{32}', state.get('InvocationID', ''))
            and int(state.get('MainPID', '0')) > 0
            and state.get('ControlGroup') == '/system.slice/' + unit,
            'INCIDENT_UNIT_STATE')
    group = Path('/sys/fs/cgroup' + state['ControlGroup'])
    require(group.is_dir() and 'populated 1' in (group / 'cgroup.events').read_text(),
            'INCIDENT_CGROUP_STATE')
    return state, group, group.stat().st_ino


def assert_self(unit):
    state, _, _ = identity(unit)
    require(int(state['MainPID']) == os.getpid()
            and Path('/proc/self/cgroup').read_text().strip() == '0::' + state['ControlGroup'],
            'INCIDENT_UNIT_SELF')


def cleanup(unit):
    # Address only this newly minted unit; no application services or cgroups.
    ctl('stop', unit)
    ctl('reset-failed', unit)


def managed(code, encoded_self, source, run):
    require(os.getuid() == 0 and type(code) is str and type(encoded_self) is str,
            'INCIDENT_ROOT')
    unit = new_unit(source, run)
    inner = ("import base64,types\n"
             "lifetime=types.ModuleType('incident_lifetime')\n"
             "exec(compile(base64.b64decode(" + repr(encoded_self) +
             "),'incident_lifetime','exec'),lifetime.__dict__)\n"
             "lifetime.assert_self(" + repr(unit) + ")\n" + code)
    try:
        result = subprocess.run(command(unit, inner), timeout=WRAPPER_SECONDS,
                                env={'PATH': '/usr/bin:/bin'}, close_fds=True)
        return result.returncode
    finally:
        cleanup(unit)


class Supervisor:
    """Read-only identity and all-scope orphan check for this incident unit."""

    def __init__(self, source, run):
        group = Path('/proc/self/cgroup').read_text().strip()
        require(group.startswith('0::/system.slice/'), 'INCIDENT_SELF_CGROUP')
        self.unit = group.removeprefix('0::/system.slice/')
        require(re.fullmatch(UNIT, self.unit)
                and self.unit.startswith('bridge-native-ro-' + source[:12] + '-'
                                         + str(run.run_id) + '-' + str(run.attempt) + '-'),
                'INCIDENT_SELF_RUN')
        assert_self(self.unit)
        state, _, inode = identity(self.unit)
        self.record = dict(unit=self.unit, invocation=state['InvocationID'], cgroup_inode=inode)
        self.failed = False

    def assert_alive(self):
        require(not self.failed, 'INCIDENT_SUPERVISOR_FAILED')
        try:
            assert_self(self.unit)
            state, _, inode = identity(self.unit)
            require(self.record == dict(unit=self.unit, invocation=state['InvocationID'],
                                        cgroup_inode=inode), 'INCIDENT_SUPERVISOR_CHANGED')
        except BaseException:
            self.failed = True
            raise

    def assert_exclusive(self):
        self.assert_alive()
        result = ctl('list-units', 'bridge-native-ro-*', '--all', '--plain', '--no-legend', '--no-pager')
        require(result.returncode == 0 and len(result.stdout) <= 65536,
                'INCIDENT_UNIT_INVENTORY')
        names = []
        for line in result.stdout.decode('ascii').splitlines():
            fields = line.split()
            require(len(fields) >= 4 and re.fullmatch(UNIT, fields[0]),
                    'INCIDENT_UNIT_INVENTORY')
            names.append(fields[0])
        require(self.unit in names and len(names) <= 128 and len(names) == len(set(names)),
                'INCIDENT_UNIT_INVENTORY')
        groups = list(Path('/sys/fs/cgroup/system.slice').iterdir())
        require(len(groups) <= 4096, 'INCIDENT_CGROUP_INVENTORY')
        matching = [group for group in groups if group.name.startswith('bridge-native-ro-')]
        require(len(matching) <= 128 and self.unit in {group.name for group in matching}
                and all(re.fullmatch(UNIT, group.name) and group.name in names for group in matching),
                'INCIDENT_UNLISTED_CGROUP')
        for name in names:
            if name == self.unit:
                continue
            state = show(name)
            require(state.get('ActiveState') in ('inactive', 'failed')
                    and state.get('MainPID') == '0', 'INCIDENT_OTHER_SUPERVISOR_ACTIVE')
            group = Path('/sys/fs/cgroup/system.slice', name)
            if group.exists():
                require(group.is_dir() and 'populated 0' in (group / 'cgroup.events').read_text(),
                        'INCIDENT_OTHER_CGROUP_ACTIVE')
        self.assert_alive()


def channel(reader, writer, binding):
    """Construct an incident subclass with one absolute 700s deadline.

    Its constructor limits seconds to historical STAGE_RPC_SECONDS; this fixed
    incident profile sets the identical fields after validating descriptors,
    without ever constructing then extending a shorter deadline.
    """
    from ops import native_maintenance_checkpoint_transport as rpc
    from ops import native_maintenance_snapshot as snapshot

    class IncidentChannel(rpc.Channel):
        def __init__(self, reader_fd, writer_fd, accepted_binding):
            require(snapshot._hex(accepted_binding)
                    and type(reader_fd) is int and reader_fd >= 0
                    and type(writer_fd) is int and writer_fd >= 0
                    and reader_fd != writer_fd, 'INCIDENT_RPC_PROFILE')
            self.reader, self.writer, self.binding = reader_fd, writer_fd, accepted_binding
            self.deadline = time.monotonic() + RPC_SECONDS
            self.failed = False
            os.set_blocking(reader_fd, False)
            os.set_blocking(writer_fd, False)

    return IncidentChannel(reader, writer, binding)
