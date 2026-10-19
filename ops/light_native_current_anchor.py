"""Pure validation of trusted current-capture bytes; OBSERVE_CURRENT only.

The witness hash is an integrity input from the authenticated root capture
callback, not human acceptance or historical continuity. The baseline SHA is a
separate external inventory pin. No I/O, HoldIdentity conversion or consumer is
provided; missing historical Ti remains UNKNOWN.
"""
from dataclasses import dataclass, fields
import hashlib
import json
import re
import stat


class AnchorUnknown(ValueError):
    """Current evidence is missing, inconsistent or stale; no authority follows."""


def _need(condition, code):
    if not condition:
        raise AnchorUnknown('CURRENT_ANCHOR_UNKNOWN_' + code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value, length=64):
    _need(type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value), 'PIN')


def _keys(value, expected):
    _need(type(value) is dict and set(value) == set(expected), 'SCHEMA')


def _integer(value, minimum=0, maximum=2**63 - 1):
    _need(type(value) is int and minimum <= value <= maximum, 'INTEGER')


def _path(value):
    _need(type(value) is str and re.fullmatch(r'/[A-Za-z0-9_./@-]{1,1023}', value) and
          all(part not in ('', '.', '..') for part in value.split('/')[1:]), 'PATH')


def _unique(pairs):
    value = {}
    for key, item in pairs:
        _need(key not in value, 'DUPLICATE_KEY')
        value[key] = item
    return value


def _number(_):
    raise AnchorUnknown('CURRENT_ANCHOR_UNKNOWN_NUMBER')


def _load(raw):
    _need(type(raw) is bytes and 0 < len(raw) <= 65536, 'BYTES')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                           parse_float=_number, parse_constant=_number)
        _need(_canonical(value) == raw, 'CANONICAL')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, AnchorUnknown):
            raise
        raise AnchorUnknown('CURRENT_ANCHOR_UNKNOWN_JSON') from None


def _hardening(release, service, value, digest):
    root = release.rsplit('/releases/', 1)[0]
    expected = dict(User='school-autopilot', Group='school-autopilot', UMask='0077', Nice='10',
        CPUQuotaPerSecUSec='1s', MemoryHigh='536870912', MemoryMax='805306368', TasksMax='64',
        NoNewPrivileges='yes', PrivateTmp='yes', PrivateDevices='yes', ProtectHome='yes',
        ProtectSystem='strict', ProtectKernelTunables='yes', ProtectKernelModules='yes',
        ProtectControlGroups='yes', RestrictSUIDSGID='yes', ReadWritePaths=root + '/runtime',
        KillMode='control-group', Restart='on-failure', TimeoutStopUSec='30s')
    _need(value == expected and service['User'] == service['Group'] == expected['User'], 'HARDENING')
    _hash(digest)
    _need(_sha(_canonical(value)) == digest, 'HARDENING_DIGEST')


def _service(value, release, state, admission):
    _keys(value, ('ActiveState', 'SubState', 'MainPID', 'NRestarts', 'InvocationID',
        'WorkingDirectory', 'User', 'Group', 'Environment', 'EnvironmentFiles', 'ExecStart',
        'FragmentPath', 'DropInPaths', 'NeedDaemonReload'))
    _need(all(type(v) is str and len(v.encode()) <= 16384 for k, v in value.items() if k != 'EnvironmentFiles'), 'SERVICE_TYPE')
    _need(type(value['EnvironmentFiles']) is list and len(value['EnvironmentFiles']) == 2 and
          all(type(item) is str and len(item) <= 1024 for item in value['EnvironmentFiles']), 'ENVIRONMENT_FILES')
    _need(re.fullmatch(r'0|[1-9][0-9]{0,9}', value['MainPID']) and
          re.fullmatch(r'0|[1-9][0-9]{0,18}', value['NRestarts']), 'PID')
    pid = int(value['MainPID'])
    _integer(pid, maximum=2**31 - 1)
    _need(state in ('RUNNING', 'STOPPED') and admission in ('ACTIVE', 'HOLD'), 'STATE')
    if state == 'RUNNING':
        _need(value['ActiveState'] == 'active' and value['SubState'] == 'running' and pid > 0, 'RUNNING')
        _hash(value['InvocationID'], 32)
    else:
        _need(value['ActiveState'] == 'inactive' and value['SubState'] == 'dead' and pid == 0 and admission == 'HOLD', 'STOPPED')
        _need(value['InvocationID'] == '' or re.fullmatch('[0-9a-f]{32}', value['InvocationID']), 'INVOCATION')
    _need(value['WorkingDirectory'] == release and value['NeedDaemonReload'] == 'no', 'LOADED_CONFIG')
    modes = [item for item in value['Environment'].split() if item.startswith('AUTOPILOT_ADMISSION_MODE=')]
    _need(modes == ['AUTOPILOT_ADMISSION_MODE=' + admission], 'ADMISSION')
    for path in (value['FragmentPath'], value['DropInPaths']):
        _path(path)
    _need(value['FragmentPath'].endswith('.service'), 'UNIT_PATH')
    match = re.fullmatch(r'\{ (path=(/[A-Za-z0-9_./-]+) ; argv\[\]=\2 -m oracle_autopilot\.worker_v17 ; ignore_errors=no) ; '
        r'start_time=\[[^\]\n]*\] ; stop_time=\[[^\]\n]*\] ; pid=[0-9]+ ; code=[^;{}\n]+ ; status=[^;{}\n]+ \}', value['ExecStart'])
    _need(match is not None, 'EXECUTION_CONTRACT')
    _path(match.group(2))
    return pid, match.group(1), match.group(2)


def _protected(witness, release, service):
    protected, metadata = witness['protected'], witness['protected_metadata']
    _need(type(protected) is dict and len(protected) == 5 and type(metadata) is dict and set(metadata) == set(protected), 'PROTECTED_INVENTORY')
    _hash(witness['protected_sha256'])
    _need(_sha(_canonical(protected)) == witness['protected_sha256'], 'PROTECTED_DIGEST')
    pin_path = release + '/ops/autopilot/broker-hold.env'
    suffix = ' (ignore_errors=no)'
    first_env, second_env = service['EnvironmentFiles']
    _need(first_env.endswith(suffix) and second_env == pin_path + suffix, 'ENVIRONMENT_FILES')
    environment_path = first_env[:-len(suffix)]
    _path(environment_path)
    names = [service['FragmentPath'], service['DropInPaths'], environment_path, pin_path]
    _need(len(set(names)) == 4 and set(names) <= set(protected), 'PROTECTED_ROLES')
    remaining = set(protected) - set(names)
    _need(len(remaining) == 1, 'PROTECTED_ROLES')
    route_path = next(iter(remaining))
    expected = dict(zip(names, ((0o644, 65536), (0o644, 4096), (0o600, 262144), (0o444, 4096))))
    expected[route_path] = (0o644, 4096)
    identities = set()
    for name, (mode, limit) in expected.items():
        _path(name)
        row, info = protected[name], metadata[name]
        _keys(row, ('mode', 'limit', 'sha256'))
        _need(type(row['mode']) is int and type(row['limit']) is int and
              (row['mode'], row['limit']) == (mode, limit), 'PROTECTED_MODE')
        _hash(row['sha256'])
        _need(type(info) is list and len(info) == 8, 'ROOT_METADATA')
        for item in info:
            _integer(item)
        device, inode, uid, gid, actual_mode, size, mtime, ctime = info
        _need(inode > 0 and uid == 0 and actual_mode == stat.S_IFREG | mode and size <= limit and
              (device, inode) not in identities, 'ROOT_METADATA')
        identities.add((device, inode))
    inputs = witness['fingerprint_inputs']
    _keys(inputs, ('service', 'environment', 'pins', 'route', 'drop'))
    _need(inputs['service'] == service, 'FINGERPRINT_SERVICE')
    for role, name in (('environment', environment_path), ('pins', pin_path), ('route', route_path), ('drop', service['DropInPaths'])):
        _hash(inputs[role])
        _need(inputs[role] == protected[name]['sha256'], 'FINGERPRINT_CONFIG')
    _hash(witness['fingerprint_sha256'])
    _need(_sha(_canonical(inputs)) == witness['fingerprint_sha256'], 'FINGERPRINT_DIGEST')
    drop = ('[Service]\nWorkingDirectory=' + release + '\nEnvironment=AUTOPILOT_ADMISSION_MODE=' +
            witness['admission'] + '\nEnvironmentFile=' + pin_path + '\n').encode()
    _need(_sha(drop) == protected[service['DropInPaths']]['sha256'] and
          metadata[service['DropInPaths']][5] == len(drop), 'DROP_DIGEST')


def _process(value, service, release, state, pid, python):
    if state == 'RUNNING':
        _keys(value, ('kind', 'pid', 'uid', 'gid', 'uids', 'gids', 'cwd', 'executable', 'argv', 'start_ticks', 'environment_sha256'))
        _need(value['kind'] == 'RUNNING_PROCESS' and type(value['pid']) is int and value['pid'] == pid, 'PROCESS_PID')
        for key in ('uid', 'gid'):
            _integer(value[key], 1, 2**32 - 1)
            ids = value[key + 's']
            _need(type(ids) is list and len(ids) == 4, 'PROCESS_IDS')
            _need(all(type(item) is int and item == value[key] for item in ids), 'PROCESS_IDS')
        _integer(value['start_ticks'], 1)
        _need(value['cwd'] == release and value['argv'] == [python, '-m', 'oracle_autopilot.worker_v17'], 'PROCESS_EXECUTION')
        _path(value['executable'])
        _hash(value['environment_sha256'])
    else:
        _keys(value, ('kind', 'MainPID', 'ControlPID', 'ActiveState', 'ControlGroup', 'cgroup_present', 'cgroup_populated'))
        unit = service['FragmentPath'].rsplit('/', 1)[-1]
        _need(value['kind'] == 'STOPPED_ABSENCE' and value['MainPID'] == value['ControlPID'] == '0' and
              value['ActiveState'] == 'inactive' and value['ControlGroup'] in ('', '/system.slice/' + unit) and
              type(value['cgroup_present']) is bool and
              value['cgroup_populated'] == ('0' if value['cgroup_present'] else None), 'STOPPED_ABSENCE')


@dataclass(frozen=True, repr=False)
class CurrentAnchor:
    witness_sha256: str
    original_baseline_sha256: str
    controller_source: str
    controller_sha256: str
    runtime_source: str
    runtime_bundle_sha256: str
    observed_at: int
    state: str
    admission: str
    _witness_raw: bytes
    assurance: str = 'OBSERVE_ONLY'
    historical_chain_verified: bool = False
    historical_baseline_matched: bool = False
    continuity_verified: bool = False
    retirement_authorized: bool = False
    admission_authorized: bool = False
    new_task_authorized: bool = False
    replay_authorized: bool = False


def validate_current_anchor(witness_raw, accepted_witness_sha256, expected_controller_source,
                            expected_controller_sha256, expected_runtime_bundle_sha256,
                            original_baseline_sha256, now):
    """Validate actual capture shape and integrity; baseline contents stay external.

    Only the source-pinned root callback supplies witness bytes and their digest.
    A caller-controlled report/hash pair is not an authenticated current capture.
    """
    for digest in (accepted_witness_sha256, expected_controller_sha256, expected_runtime_bundle_sha256, original_baseline_sha256):
        _hash(digest)
    _hash(expected_controller_source, 40)
    _integer(now)
    value = _load(witness_raw)
    _need(_sha(witness_raw) == accepted_witness_sha256, 'WITNESS_DIGEST')
    _keys(value, ('version', 'audit', 'assurance', 'observed_at', 'capture_started_at',
        'controller_source', 'controller_package_sha256', 'runtime_source', 'runtime_bundle_sha256',
        'installed_release', 'historical_chain_verified', 'admission_authorized', 'retirement_authorized',
        'hostname', 'state', 'admission', 'service', 'fingerprint_inputs', 'fingerprint_sha256',
        'execution_contract', 'protected', 'protected_metadata', 'protected_sha256', 'hardening', 'hardening_sha256', 'process'))
    _need(type(value['version']) is int and value['version'] == 1 and value['audit'] == 'LEGACY_CURRENT_CAPTURE' and
          value['assurance'] == 'CURRENT_OBSERVATION_ONLY', 'SCOPE')
    _need(all(value[key] is False for key in ('historical_chain_verified', 'admission_authorized', 'retirement_authorized')), 'NO_AUTHORITY')
    _need(value['controller_source'] == expected_controller_source and
          value['controller_package_sha256'] == expected_controller_sha256 and
          value['runtime_bundle_sha256'] == expected_runtime_bundle_sha256, 'SOURCE_BINDING')
    _hash(value['runtime_source'], 40)
    release = value['installed_release']
    _path(release)
    _need('/releases/' in release and release.rsplit('/releases/', 1)[1] == value['runtime_source'], 'RUNTIME_BINDING')
    _need(type(value['hostname']) is str and re.fullmatch('[A-Za-z0-9.-]{1,253}', value['hostname']), 'HOST')
    _integer(value['observed_at'])
    _integer(value['capture_started_at'])
    _need(value['capture_started_at'] <= value['observed_at'] <= value['capture_started_at'] + 30 and
          0 <= now - value['observed_at'] <= 30, 'FRESHNESS')
    pid, contract, python = _service(value['service'], release, value['state'], value['admission'])
    _need(value['execution_contract'] == contract, 'EXECUTION_BINDING')
    _hardening(release, value['service'], value['hardening'], value['hardening_sha256'])
    _protected(value, release, value['service'])
    _process(value['process'], value['service'], release, value['state'], pid, python)
    return CurrentAnchor(accepted_witness_sha256, original_baseline_sha256, expected_controller_source,
        expected_controller_sha256, value['runtime_source'], expected_runtime_bundle_sha256,
        value['observed_at'], value['state'], value['admission'], witness_raw)


def require_current_observation(anchor, *, purpose, accepted_witness_sha256, original_baseline_sha256, now):
    """Revalidate retained bytes and their exact projection; OBSERVE_CURRENT only."""
    _need(type(anchor) is CurrentAnchor and anchor.assurance == 'OBSERVE_ONLY', 'TYPE')
    _need(purpose == 'OBSERVE_CURRENT', 'PURPOSE')
    _hash(accepted_witness_sha256)
    _hash(original_baseline_sha256)
    _integer(now)
    _need(anchor.witness_sha256 == accepted_witness_sha256 and
          anchor.original_baseline_sha256 == original_baseline_sha256, 'REPIN')
    _need(all(getattr(anchor, key) is False for key in ('historical_chain_verified', 'historical_baseline_matched',
        'continuity_verified', 'retirement_authorized', 'admission_authorized', 'new_task_authorized', 'replay_authorized')), 'NO_AUTHORITY')
    verified = validate_current_anchor(anchor._witness_raw, accepted_witness_sha256,
        anchor.controller_source, anchor.controller_sha256, anchor.runtime_bundle_sha256,
        original_baseline_sha256, now)
    # Frozen dataclasses still support replace(). Never trust projected fields
    # (especially freshness/source/state) independently of the retained bytes.
    # Exact types also reject int/float/bool equality aliases in replacements.
    _need(all(type(getattr(anchor, item.name)) is type(getattr(verified, item.name)) and
              getattr(anchor, item.name) == getattr(verified, item.name)
              for item in fields(CurrentAnchor)), 'PROJECTION')
    return anchor


def anchor_projection(anchor):
    """JSON-safe digest preimage without witness bytes; emit only its hash.

    This repeats structural/projection validation at the captured time. A caller
    must first run require_current_observation with trusted *current* time and
    external witness/baseline pins. This helper cannot establish freshness or
    authenticity of those external inputs by itself.
    """
    _need(type(anchor) is CurrentAnchor, 'TYPE')
    require_current_observation(anchor, purpose='OBSERVE_CURRENT',
        accepted_witness_sha256=anchor.witness_sha256,
        original_baseline_sha256=anchor.original_baseline_sha256, now=anchor.observed_at)
    names = ('witness_sha256', 'original_baseline_sha256', 'controller_source', 'controller_sha256',
        'runtime_source', 'runtime_bundle_sha256', 'observed_at', 'state', 'admission', 'assurance',
        'historical_chain_verified', 'historical_baseline_matched', 'continuity_verified',
        'retirement_authorized', 'admission_authorized', 'new_task_authorized', 'replay_authorized')
    return {name: getattr(anchor, name) for name in names}
