"""Fixed transient service plan for one native pilot; no launch side effects.

The existing enabled HOLD unit is never edited. The caller must arm the root
supervisor and its fixed restoration hook before stopping that unit. Unit
rendering alone is not admission, an accepted permit, or terminal evidence.
"""
from pathlib import Path
import re

from ops import oracle_light_active_hold_attest as hold
from ops import light_native_pilot_release as release

ROOT = Path('/var/lib/bridge-light-native-pilot')
LIGHT = Path('/opt/bridge-school/school-autopilot-production-light')
PILOT_UNIT = 'bridge-light-native-single-pilot.service'
SUPERVISOR_UNIT = 'bridge-light-native-single-supervisor.service'
PYTHON = release.PYTHON

# Pin every sandbox/resource setting from the currently attested persistent
# unit. None is widened for the transient executor.
HARDENING = {
    'User': 'school-autopilot', 'Group': 'school-autopilot', 'UMask': '0077',
    'Nice': '10', 'CPUQuota': '100%', 'MemoryHigh': '512M', 'MemoryMax': '768M',
    'TasksMax': '64', 'NoNewPrivileges': 'yes', 'PrivateTmp': 'yes',
    'PrivateDevices': 'yes', 'ProtectHome': 'yes', 'ProtectSystem': 'strict',
    'ProtectKernelTunables': 'yes', 'ProtectKernelModules': 'yes',
    'ProtectControlGroups': 'yes', 'RestrictSUIDSGID': 'yes',
    'ReadWritePaths': str(LIGHT / 'runtime'), 'KillMode': 'control-group',
    'Restart': 'no', 'TimeoutStopSec': '30',
}


def require(value, code):
    if not value:
        raise RuntimeError(code)


def source_path(source):
    require(type(source) is str and re.fullmatch('[0-9a-f]{40}', source), 'PILOT_SERVICE_SOURCE')
    return LIGHT / 'releases' / source


def check_prior(prior):
    require(type(prior) is hold.HoldIdentity and prior.hostname == 'autopilot-lite-vnic'
            and prior.release.startswith(str(LIGHT / 'releases') + '/'), 'PILOT_SERVICE_PRIOR')
    require(str(source_path(Path(prior.release).name)) == prior.release, 'PILOT_SERVICE_PRIOR_PATH')


def pilot_properties(source, prior, seconds, *, request_digest):
    check_prior(prior)
    require(type(request_digest) is str and re.fullmatch('[0-9a-f]{64}',request_digest), 'PILOT_SERVICE_REQUEST_DIGEST')
    require(type(seconds) is int and 30 <= seconds <= 1800, 'PILOT_SERVICE_DURATION')
    candidate = source_path(source)
    require(str(candidate) != prior.release, 'PILOT_SERVICE_SAME_RELEASE')
    return {**HARDENING, 'Description': 'Bridge native pilot '+request_digest, 'WorkingDirectory': str(candidate), 'RuntimeMaxSec': str(seconds),
            'EnvironmentFile': [str(hold.ENV), prior.release + '/ops/autopilot/broker-hold.env'],
            'Environment': ['PYTHONDONTWRITEBYTECODE=1', 'PYTHONUNBUFFERED=1',
                            'AUTOPILOT_ADMISSION_MODE=PILOT']}


def pilot_command(source, prior, seconds, *, request_digest):
    command = ['/usr/bin/systemd-run', '--quiet', '--unit=' + PILOT_UNIT, '--service-type=exec']
    for name, value in pilot_properties(source, prior, seconds, request_digest=request_digest).items():
        if isinstance(value, list):
            for item in value:
                command.append('--property=' + name + '=' + item)
        else:
            command.append('--property=' + name + '=' + value)
    return command + ['--'] + pilot_argv(source)


def pilot_argv(source):
    """Same pinned interpreter/gate for initial launch and controlled reentry."""
    return [PYTHON, '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv[1]);'
        'from oracle_autopilot.light_native_launch_gate import main;main()', str(source_path(source))]


def supervisor_command(source, accepted_request, seconds):
    source_path(source)
    require(type(accepted_request) is str and re.fullmatch('[0-9a-f]{64}', accepted_request),
            'PILOT_SERVICE_REQUEST_DIGEST')
    require(type(seconds) is int and 60 <= seconds <= 1800, 'PILOT_SERVICE_DURATION')
    script = ROOT / accepted_request / 'supervisor.py'
    restore = '/usr/bin/python3 -I -S -B ' + str(script) + ' restore ' + accepted_request
    # The source-defined private script is retained and independently checked
    # before systemd sees this command. It must survive the launcher process.
    return ['/usr/bin/systemd-run', '--quiet', '--unit=' + SUPERVISOR_UNIT, '--service-type=exec',
            '--property=User=root', '--property=Group=root', '--property=UMask=0077',
            '--property=NoNewPrivileges=yes', '--property=Restart=no',
            '--property=KillMode=control-group', '--property=TimeoutStopSec=120',
            '--property=RuntimeMaxSec=' + str(seconds), '--property=ExecStopPost=' + restore,
            '--', '/usr/bin/python3', '-I', '-S', '-B', str(script), 'run', accepted_request]


def classify_journal(lines, dispatch_id):
    """A wake-up signal only; independent DB/provider readback decides success."""
    import json
    require(type(lines) is str and len(lines.encode()) <= 65536, 'PILOT_SERVICE_JOURNAL_SIZE')
    terminal = []
    quarantined = False
    for line in lines.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if type(obj) is not dict:
            continue
        if obj.get('audit') == 'LIGHT_NATIVE_SINGLE_PILOT_QUARANTINED':
            quarantined = True
        if obj.get('audit') == 'LIGHT_NATIVE_SINGLE_PILOT_TERMINAL':
            require(set(obj) == {'audit', 'dispatch_id', 'provider_task_id', 'terminal_sha256'}
                    and obj['dispatch_id'] == dispatch_id
                    and type(obj['provider_task_id']) is str
                    # Match the provider and native_cli_receipt SQL contract.
                    # This isolated supervisor cannot import the runtime package.
                    and re.fullmatch('task_[A-Za-z0-9_]{1,120}', obj['provider_task_id'])
                    and type(obj['terminal_sha256']) is str
                    and re.fullmatch('[0-9a-f]{64}', obj['terminal_sha256']), 'PILOT_SERVICE_TERMINAL_MARKER')
            terminal.append(obj)
    require(len(terminal) <= 1 and not (quarantined and terminal), 'PILOT_SERVICE_AMBIGUOUS_OUTCOME')
    if quarantined:
        return {'state': 'QUARANTINED'}
    if terminal:
        return {'state': 'READBACK_REQUIRED', **terminal[0]}
    return {'state': 'RUNNING'}
