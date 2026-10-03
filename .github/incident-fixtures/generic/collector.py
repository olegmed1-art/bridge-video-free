"""Read-only local journal and service continuity collector. Site identities require a private, hash-pinned owner configuration; no defaults. No writes or RPC."""
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import time
CONFIG_SCHEMA = {'identity_00': 'str', 'identity_01': 'str', 'identity_02': 'str', 'identity_03': 'str', 'identity_04': 'str', 'identity_05': 'str', 'identity_06': 'str', 'identity_07': 'str', 'identity_08': 'str', 'identity_09': 'str', 'identity_10': 'str', 'identity_11': 'str', 'identity_12': 'str', 'identity_13': 'str', 'identity_14': 'str', 'identity_15': 'str', 'identity_16': 'str', 'identity_17': 'str', 'identity_18': 'str', 'identity_19': 'str', 'identity_20': 'str', 'identity_21': 'str', 'identity_22': 'str', 'identity_23': 'str', 'identity_24': 'str', 'identity_25': 'str', 'identity_26': 'str', 'identity_27': 'str', 'identity_28': 'str', 'identity_29': 'str', 'identity_30': 'str', 'identity_31': 'str', 'identity_32': 'str', 'identity_33': 'str', 'identity_34': 'str', 'identity_35': 'str', 'identity_36': 'str', 'identity_37': 'str', 'target_number': 'int'}

def _configuration():
    try:
        raw = globals().get('PRIVATE_CONFIG_BYTES')
        digest = globals().get('PRIVATE_CONFIG_SHA256')
        if type(raw) is not bytes or len(raw) > 32768 or type(digest) is not str:
            raise ValueError()
        if len(digest) != 64 or any((c not in '0123456789abcdef' for c in digest)):
            raise ValueError()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError()

        def pairs(rows):
            result = {}
            for key, value in rows:
                if key in result:
                    raise ValueError()
                result[key] = value
            return result
        parsed = json.loads(raw, object_pairs_hook=pairs)
        if type(parsed) is not dict or set(parsed) != set(CONFIG_SCHEMA):
            raise ValueError()
        if json.dumps(parsed, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode() != raw:
            raise ValueError()
        for key, kind in CONFIG_SCHEMA.items():
            value = parsed[key]
            if type(value).__name__ != kind:
                raise ValueError()
            if kind == 'str' and (not value or len(value) > 2048 or '\x00' in value):
                raise ValueError()
            if kind == 'int' and value <= 0:
                raise ValueError()
        from types import MappingProxyType
        return MappingProxyType(parsed)
    except BaseException:
        print('{"issue_allowed":false,"stage":"CONFIGURATION","state":"REFUSED"}')
        raise SystemExit(2)
CFG = _configuration()
POLICY = CFG['identity_01']
PLAN = CFG['identity_02']
BASE = CFG['identity_03']
SCOPE = BASE + '/' + PLAN
ISSUER = BASE + '/issuers/' + POLICY
CYCLE = BASE + '/cycles/' + PLAN
RUNTIME = CFG['identity_04']
LIGHT = CFG['identity_05']
RELEASE = LIGHT + '/releases/' + RUNTIME
STATE = LIGHT + '/runtime/native-lane'
CONTROL = CFG['identity_06']
LEDGER = CFG['identity_07']
NATIVE = CFG['identity_08']
LEGACY = CFG['identity_09']
EXECUTOR = CFG['identity_10']
PYTHON = CFG['identity_11']
LAUNCH = CFG['identity_12']
COVERAGE = ['direct_owner_sql', 'host_administration', 'workflow_administration', 'workflow_reruns', 'main_pushes']
FALSE_FLAGS = ('repository_mutation', 'production_mutation', 'canon_mutation', CFG['identity_13'], 'server_mutation', 'external_mutation', 'media_execution', 'paid_action', 'merge', 'deploy')
HARDENING = {'User': CFG['identity_14'], 'Group': CFG['identity_14'], 'UMask': '0077', 'Nice': '10', 'CPUQuota': '100%', 'MemoryHigh': '512M', 'MemoryMax': '768M', 'TasksMax': '64', 'NoNewPrivileges': 'yes', 'PrivateTmp': 'yes', 'PrivateDevices': 'yes', 'ProtectHome': 'yes', 'ProtectSystem': 'strict', 'ProtectKernelTunables': 'yes', 'ProtectKernelModules': 'yes', 'ProtectControlGroups': 'yes', 'RestrictSUIDSGID': 'yes', 'ReadWritePaths': LIGHT + '/runtime', 'KillMode': 'control-group', 'Restart': 'no', 'TimeoutStopSec': '30'}
TARGET = dict(database=CFG['identity_18'], session_owner=CFG['identity_19'], owner=CFG['identity_19'], recipient=CFG['identity_20'], neon=dict(project_id=CFG['identity_25'], branch_id=CFG['identity_26'], endpoint_id=CFG['identity_27'], host=CFG['identity_28']))
PINS = {'policy.json': POLICY, 'plan.json': PLAN}
PRIVATE_PIN_KEYS = ('issuer-intent', 'cycle-intent', 'prepare.json', 'baseline.json', 'before.json', 'incident.json', 'contained.json', 'driver-contain-intent', 'cycle-contain-intent', 'runtime-package.json', 'wheels.tar')
STAGES = ('START', 'JOURNAL_SNAPSHOT', 'POLICY_BINDINGS', 'PREDECESSOR_LOCAL', 'LEGACY_HOLD', 'NATIVE_HOLD', 'STABLE_READBACK')
STAGE = 'START'

class Refused(Exception):
    pass

def need(ok):
    if not ok:
        raise Refused()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def enc(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()

def hexid(value, n=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(n) + '}', value) is not None

def parse(raw, ascii=False):

    def pairs(rows):
        out = {}
        for key, val in rows:
            need(key not in out)
            out[key] = val
        return out
    value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))
    need(type(value) is dict and json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=ascii).encode() == raw)
    return value

def stamp(value):
    need(type(value) is str and re.fullmatch('\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\dZ', value))
    return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())

def meta(s):
    return (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

class Reader:
    """No symlink ancestors, no atime fallback; detect replacement/content drift."""

    def __init__(self):
        self.fds = {}
        self.checks = []
        self.total = 0

    def directory(self, path):
        need(path.startswith('/') and '..' not in path.split('/') and ('//' not in path))
        if path in self.fds:
            return self.fds[path]
        if path == '/':
            fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
        else:
            parent, name = path.rsplit('/', 1)
            pfd = self.directory(parent or '/')
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=pfd)
            self.checks.append(('link', pfd, name, fd, meta(os.fstat(fd))))
        self.fds[path] = fd
        s = os.fstat(fd)
        service = path in (LIGHT + '/runtime', STATE) or path.startswith(STATE + '/')
        uid = pwd.getpwnam(CFG['identity_14']).pw_uid if service else 0
        need(s.st_uid == uid and (not stat.S_IMODE(s.st_mode) & 18))
        if path.startswith(BASE + '/') or path == BASE or path.startswith(STATE):
            need(stat.S_IMODE(s.st_mode) == 448)
        return fd

    def names(self, path):
        fd = self.directory(path)
        names = sorted(os.listdir(fd))
        need(len(names) <= 128)
        self.checks.append(('names', fd, names, meta(os.fstat(fd))))
        return set(names)

    def read(self, path, mode=384, limit=1048576, uid=0, gid=None):
        parent, name = path.rsplit('/', 1)
        pfd = self.directory(parent)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=pfd)
        try:
            s = os.fstat(fd)
            need(stat.S_ISREG(s.st_mode) and s.st_uid == uid and (s.st_nlink == 1) and (stat.S_IMODE(s.st_mode) == mode) and (0 < s.st_size <= limit) and (gid is None or s.st_gid == gid))
            chunks = []
            size = 0
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                size += len(chunk)
                need(size <= limit)
                chunks.append(chunk)
            raw = b''.join(chunks)
            self.total += size
            need(self.total <= 32 * 1024 * 1024)
            need(size == s.st_size and meta(s) == meta(os.fstat(fd)) == meta(os.stat(name, dir_fd=pfd, follow_symlinks=False)))
            self.checks.append(('file', pfd, name, meta(s), sha(raw)))
            return raw
        finally:
            os.close(fd)

    def lock(self, path):
        parent, name = path.rsplit('/', 1)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=self.directory(parent))
        self.fds['lock:' + path] = fd
        s = os.fstat(fd)
        need(stat.S_ISREG(s.st_mode) and s.st_uid == s.st_gid == 0 and (s.st_nlink == 1) and (stat.S_IMODE(s.st_mode) == 384))
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        self.checks.append(('link', self.directory(parent), name, fd, meta(s)))

    def recheck(self):
        for item in list(self.checks):
            kind = item[0]
            if kind == 'link':
                _, parent, name, fd, original = item
                need(meta(os.stat(name, dir_fd=parent, follow_symlinks=False)) == meta(os.fstat(fd)) == original)
            elif kind == 'names':
                _, fd, names, m = item
                need(sorted(os.listdir(fd)) == names and meta(os.fstat(fd)) == m)
            else:
                _, parent, name, m, digest = item
                need(meta(os.stat(name, dir_fd=parent, follow_symlinks=False)) == m)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK, dir_fd=parent)
                try:
                    need(meta(os.fstat(fd)) == m)
                    h = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = os.read(fd, 65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        need(size <= m[6])
                        h.update(chunk)
                    need(size == m[6] and h.hexdigest() == digest and (meta(os.fstat(fd)) == m))
                finally:
                    os.close(fd)

    def close(self):
        for fd in reversed(list(self.fds.values())):
            os.close(fd)

def pinned(reader, path, key):
    raw = reader.read(path, limit=12 * 1024 * 1024)
    need(sha(raw) == PINS[key])
    return raw

def bindings(policy, issuer, cycle, prepare, plan, baseline):
    need(policy['version'] == 1 and policy['action'] == 'issue' and hexid(policy['source'], 40) and hexid(policy['accepted_controller_sha256']) and hexid(policy['accepted_runtime_sha256']))
    previous = policy['predecessor']
    need(set(previous) == {'plan_sha256', 'terminal_sha256', 'sequence'} and type(previous['sequence']) is int and (previous['sequence'] == 2) and hexid(previous['plan_sha256']) and (previous['plan_sha256'] != PLAN) and hexid(previous['terminal_sha256']))
    entries = policy['plans']
    need(type(entries) is list and 1 <= len(entries) <= 8)
    need(set(entries[0]) == {'plan_base64', 'accepted_plan_sha256'} and entries[0]['accepted_plan_sha256'] == PLAN and (base64.b64decode(entries[0]['plan_base64'], validate=True) == json.dumps(plan, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()))
    authority = policy['authority']
    need(set(authority) == {'owner', 'coverage', 'delegation', 'evidence'} and authority['owner'] == CFG['identity_23'] and (authority['coverage'] == COVERAGE) and (authority['delegation'] == 'FINITE_ISSUER_AGREEMENTS') and (type(authority['evidence']) is str) and (0 < len(authority['evidence']) <= 1024))
    need(set(issuer) == {'start', 'cycle'} and type(issuer['start']) is int)
    start = issuer['start']
    end = min(start + 1800, stamp(policy['expires_at']))
    need(stamp(policy['not_before']) <= start and 900 <= end - start <= 1800)
    fmt = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    scope = dict(version=1, operation='native_single_pilot', source=RUNTIME, target=TARGET, plan_sha256=PLAN)
    agreement = dict(version=1, owner=CFG['identity_23'], operation_digest=sha(enc(scope)), not_before=fmt(start), expires_at=fmt(end), coverage=COVERAGE, evidence=f'Explicit finite issuer policy {POLICY}; catalogue entry 0. No unlisted work, renewal, repair, spending or unknown-outcome replay.')
    expected = dict(version=2, action='prepare', source=policy['source'], accepted_controller_sha256=policy['accepted_controller_sha256'], accepted_runtime_sha256=policy['accepted_runtime_sha256'], **entries[0], agreement=agreement, accepted_agreement_sha256=sha(enc(agreement)), accepted_receipt_sha256=None, accepted_discovery_sha256=None, accepted_permit_sha256=None, accepted_terminal_sha256=None, predecessor=previous)
    need(prepare == expected and cycle == issuer['cycle'] == dict(version=1, action='cycle', prepare=expected))
    need(set(plan) == {'version', 'source', 'repository', 'target_pr', 'expected_head_sha', 'work_key', 'objective', 'priority', 'task_spec_json', 'branch'} and type(plan['version']) is int and (type(plan['target_pr']) is int) and (type(plan['priority']) is int) and (plan['priority'] in (0, 10, 20, 30)) and (type(plan['objective']) is str) and (0 < len(plan['objective']) <= 1000) and all((ord(c) >= 32 for c in plan['objective'])))
    spec = plan['task_spec_json']
    need(type(spec) is dict and len(enc(spec)) <= 4096 and (set(spec) <= {'assignment_schema', 'repository', 'target_pr', 'expected_head_sha', 'execution_mode', 'exact_head_binding', 'cost_cap_microusd', 'max_repair_attempts', *FALSE_FLAGS, 'focus_path', 'focus_paths', 'required_checks', 'preserve', 'verification_kind', 'expected_changed_files'}))
    need(plan['version'] == 1 and plan['source'] == RUNTIME and (plan['repository'] == CFG['identity_29']) and (plan['target_pr'] == CFG['target_number']) and (plan['work_key'] == CFG['identity_30']) and hexid(plan['expected_head_sha'], 40) and (type(plan['branch']) is str) and re.fullmatch(CFG['identity_31'], plan['branch']) and ('..' not in plan['branch']) and (not plan['branch'].startswith(CFG['identity_36'])))
    need(spec['assignment_schema'] == CFG['identity_32'] and spec['execution_mode'] == 'READ_ONLY' and (spec['repository'] == plan['repository']) and (spec['target_pr'] == CFG['target_number']) and (spec['expected_head_sha'] == plan['expected_head_sha']) and (spec['exact_head_binding'] is True) and all((spec[k] is False for k in FALSE_FLAGS)) and all((type(spec[k]) is int and spec[k] == 0 for k in ('cost_cap_microusd', 'max_repair_attempts'))))
    need(baseline['version'] == 1 and baseline['source'] == RUNTIME and (baseline['package_sha256'] == policy['accepted_runtime_sha256'] == PINS['runtime-package.json']) and (baseline['agreement_sha256'] == prepare['accepted_agreement_sha256']) and (baseline['scope_sha256'] == sha(enc(scope))))
    return previous

def command(args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=3, env={'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_PAGER': ''})
    need(result.returncode == 0 and len(result.stdout) <= 262144)
    return result.stdout.decode().strip()

def show(unit, fields, legacy=False):
    need(unit in (NATIVE, LEGACY, EXECUTOR))
    out = command(['/usr/bin/systemctl', 'show', unit] + ['-p' + f for f in fields])
    result = {}
    if legacy:
        result['EnvironmentFiles'] = []
    for line in out.splitlines():
        key, sep, val = line.partition('=')
        need(sep and key in fields)
        if legacy and key == 'EnvironmentFiles':
            result[key].append(val)
        else:
            need(key not in result)
            result[key] = val
    need(set(result) == set(fields))
    return result

def proc(pid, name, limit=262144):
    need(type(pid) is str and pid.isdigit() and (int(pid) > 1) and (name in ('environ', 'cmdline', 'status')))
    fd = os.open('/proc/' + pid + '/' + name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        raw = os.read(fd, limit + 1)
        need(len(raw) <= limit)
        return raw
    finally:
        os.close(fd)

def process_hold(row, release, native=False):
    pid = row['MainPID']
    need(pid.isdigit() and int(pid) > 1)
    user = pwd.getpwnam(CFG['identity_14'])
    need(os.stat('/proc/' + pid).st_uid == user.pw_uid and os.readlink('/proc/' + pid + '/cwd') == release)
    raw = proc(pid, 'environ')
    parts = [p for p in raw.split(b'\x00') if p]
    env = {}
    for part in parts:
        key, sep, value = part.partition(b'=')
        need(sep and key not in env)
        env[key] = value
    need(env.get(CFG['identity_24'].encode()) == b'HOLD')
    status = dict((line.split(':', 1) for line in proc(pid, 'status').decode().splitlines() if ':' in line))
    need(status['NoNewPrivs'].strip() == '1')
    if native:
        need(proc(pid, 'cmdline').split(b'\x00') == [s.encode() for s in [PYTHON, '-I', '-B', '-c', LAUNCH, release, '']])
        safe = {b'PATH', b'LANG', b'LANGUAGE', b'USER', b'LOGNAME', b'HOME', b'SHELL', b'INVOCATION_ID', b'JOURNAL_STREAM', b'SYSTEMD_EXEC_PID', b'MEMORY_PRESSURE_WATCH', b'MEMORY_PRESSURE_WRITE', b'PYTHONDONTWRITEBYTECODE', b'PYTHONUNBUFFERED', CFG['identity_24'].encode()}
        need(all((k in safe or k.startswith(b'LC_') for k in env)))

def legacy_hold(reader, baseline):
    prior = baseline['prior']
    release = prior['release']
    need(set(prior) == {'hostname', 'pid', 'invocation_id', 'release', 'fingerprint'} and prior['hostname'] == CFG['identity_33'] and (type(prior['pid']) is int) and re.fullmatch(re.escape(LIGHT) + '/releases/[0-9a-f]{40}', release))
    unit = '/etc/systemd/system/' + LEGACY
    drop = unit + CFG['identity_21']
    disk = CFG['identity_15']
    pins = release + CFG['identity_22']
    route = CFG['identity_16']
    expected = {unit: (420, 65536), drop: (420, 4096), disk: (384, 262144), pins: (292, 4096), route: (420, 4096)}
    protected = baseline['protected']
    need(set(protected) == set(expected) and sha(enc(protected)) == baseline['protected_sha256'])
    data = {}
    for path, (mode, limit) in expected.items():
        record = protected[path]
        need(set(record) == {'mode', 'limit', 'sha256'} and (record['mode'], record['limit']) == (mode, limit))
        data[path] = reader.read(path, mode, limit)
        need(sha(data[path]) == record['sha256'])
    fields = ('ActiveState', 'SubState', 'MainPID', 'NRestarts', 'InvocationID', 'WorkingDirectory', 'User', 'Group', 'Environment', 'EnvironmentFiles', 'ExecStart', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
    row = show(LEGACY, fields, True)
    need(row['ActiveState'] == 'active' and row['SubState'] == 'running' and (row['MainPID'] == str(prior['pid'])) and (row['InvocationID'] == prior['invocation_id']) and (row['WorkingDirectory'] == release) and (row['NeedDaemonReload'] == 'no') and (row['User'] == row['Group'] == CFG['identity_14']))
    fingerprint = sha(enc(dict(service=row, environment=sha(data[disk]), pins=sha(data[pins]), route=sha(data[route]), drop=sha(data[drop]))))
    need(fingerprint == prior['fingerprint'])
    process_hold(row, release)
    return row

def predecessor(reader, previous, prepare):
    root = BASE + '/' + previous['plan_sha256']
    names = reader.names(root)

    def raw(name):
        return reader.read(root + '/' + name)

    def obj(name):
        return parse(raw(name))
    prior_plan = raw('plan.json')
    need(sha(prior_plan) == previous['plan_sha256'])
    terminal_raw = raw('terminal.json')
    need(sha(terminal_raw) == previous['terminal_sha256'])
    receipt = obj('intake.json')
    terminal = parse(terminal_raw)
    prepared = obj('prepare.json')
    complete = obj('complete.json')
    restored = obj('controls-restored.json')
    execution = obj('execution.json')
    need(hexid(execution['request_sha256']) and prepared['accepted_plan_sha256'] == previous['plan_sha256'] and (prepared['accepted_runtime_sha256'] == prepare['accepted_runtime_sha256']) and (prepared['predecessor']['sequence'] == 1) and (parse(prior_plan, True)['source'] == RUNTIME))
    binding = enc(dict(plan_sha256=previous['plan_sha256'], request_sha256=execution['request_sha256'], receipt_sha256=sha(enc(receipt)), terminal_sha256=previous['terminal_sha256']))
    need(raw('restore-intent.json') == raw('restart-intent.json') == binding and complete['controls_restored'] is True and ({k: v for k, v in complete.items() if k != 'native'} == restored) and (terminal['plan_sha256'] == receipt['plan_sha256'] == previous['plan_sha256']) and (terminal['dispatch_id'] == receipt['dispatch_id']) and (terminal['task_id'] == receipt['task_id']))
    return (complete, receipt, terminal)

def native_hold(reader, complete, receipt, terminal):
    user = pwd.getpwnam(CFG['identity_14'])
    gid = user.pw_gid
    need(reader.names(CONTROL) == {'admission', 'current.json', 'jobs'})
    need(reader.read(CONTROL + '/admission', 416, 16, gid=gid) == b'HOLD\n')
    cursor_raw = reader.read(CONTROL + '/current.json', 416, 4096, gid=gid)
    cursor = parse(cursor_raw)
    need(cursor['source'] == RUNTIME and type(cursor['sequence']) is int and (cursor['sequence'] == 2) and (cursor['dispatch_id'] == receipt['dispatch_id']))
    names = reader.names(STATE)
    expected = {'pilot.lock'}
    dispatches = set()
    last = None
    for number in range(3):
        intent_name = f'{number:08d}-intent.json'
        terminal_name = f'{number:08d}-terminal.json'
        intent_raw = reader.read(STATE + '/' + intent_name, uid=user.pw_uid)
        entry = parse(intent_raw)
        done_raw = reader.read(STATE + '/' + terminal_name, uid=user.pw_uid)
        done = parse(done_raw)
        dispatch = entry['dispatch_id']
        need(type(dispatch) is str and re.fullmatch('[0-9a-f-]{36}', dispatch) and (dispatch not in dispatches))
        need(entry['source'] == RUNTIME and entry['sequence'] == number and (entry['previous_terminal_sha256'] == last) and (done['intent_sha256'] == sha(intent_raw)) and (done['result']['state'] == 'DONE') and (done['request']['dispatch_id'] == done['result']['dispatch_id'] == dispatch))
        claim = STATE + '/claim-' + dispatch
        reader.names(claim)
        need(reader.read(claim + '/request.json', uid=user.pw_uid) == enc(done['request']))
        permit_raw = reader.read(claim + '/permit.json', uid=user.pw_uid)
        permit = parse(permit_raw)
        need(sha(permit_raw) == entry['permit_sha256'] and permit['dispatch'] == {k: v for k, v in done['request'].items() if k != 'reservation_id'})
        job = CONTROL + '/jobs/' + dispatch
        need(reader.read(job + '/permit.json', 416, gid=gid) == permit_raw)
        acceptance = dict(version=1, source=RUNTIME, sequence=number, dispatch_id=dispatch, terminal_sha256=sha(done_raw), provider_evidence_sha256=done['result']['terminal']['provider_evidence_sha256'])
        need(reader.read(job + '/accepted-terminal.json', 416, 4096, gid=gid) == enc(acceptance))
        if number == 2:
            need(intent_raw == cursor_raw and done['request'] == terminal['request'] and (done['result']['provider_task_id'] == terminal['provider_task_id']) and (done['result']['terminal'] == terminal['result']))
        expected.update((intent_name, terminal_name, 'claim-' + dispatch))
        dispatches.add(dispatch)
        last = sha(done_raw)
    need(names == expected and reader.names(CONTROL + '/jobs') == dispatches)
    need('00000003-feed-intent.json' not in reader.names(LEDGER))
    fields = ('ActiveState', 'SubState', 'MainPID', 'NRestarts', 'InvocationID')
    row = show(NATIVE, fields)
    need(row == complete['native'] and row['ActiveState'] == 'active' and (row['SubState'] == 'running') and (row['NRestarts'] == '0'))
    unit = '/etc/systemd/system/' + NATIVE
    environment = CFG['identity_17']
    properties = {**HARDENING, 'Type': 'exec', 'WorkingDirectory': RELEASE, 'ReadWritePaths': STATE, 'PrivateNetwork': 'yes', 'Environment': environment, 'ExecStart': PYTHON + ' -I -B -c "' + LAUNCH + '" ' + RELEASE}
    rendered = (CFG['identity_34'] + ''.join((k + '=' + v + '\n' for k, v in properties.items()))).encode()
    need(reader.read(unit, 420, 8192) == rendered)
    expected = {**HARDENING, 'ReadWritePaths': STATE, 'PrivateNetwork': 'yes', 'Environment': environment, 'WorkingDirectory': RELEASE, 'Type': 'exec', 'FragmentPath': unit, 'DropInPaths': '', 'NeedDaemonReload': 'no', 'UnitFileState': 'static'}
    expected.pop('CPUQuota')
    expected.pop('TimeoutStopSec')
    expected.update(CPUQuotaPerSecUSec='1s', TimeoutStopUSec='30s', MemoryHigh='536870912', MemoryMax='805306368')
    output = command(['/usr/bin/systemctl', 'show', '--all', NATIVE] + ['-p' + k for k in expected])
    config = {}
    for line in output.splitlines():
        key, sep, val = line.partition('=')
        need(sep and key in expected and (key not in config))
        config[key] = val
    need(config == expected)
    need(command(['/usr/bin/busctl', 'get-property', 'org.freedesktop.systemd1', CFG['identity_37'], 'org.freedesktop.systemd1.Service', 'EnvironmentFiles']) == 'a(sb) 0')
    process_hold(row, RELEASE, True)
    executor = show(EXECUTOR, ('LoadState', 'ActiveState', 'MainPID', 'ControlPID', 'ControlGroup'))
    need(executor['MainPID'] == executor['ControlPID'] == '0' and executor['ActiveState'] in ('inactive', 'failed') and (executor['ControlGroup'] in ('', '/system.slice/' + EXECUTOR)))
    cgroup = '/sys/fs/cgroup/system.slice/' + EXECUTOR + '/cgroup.events'
    try:
        fd = os.open(cgroup, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        pass
    else:
        try:
            need(dict((line.split() for line in os.read(fd, 4097).decode().splitlines())).get('populated') == '0')
        finally:
            os.close(fd)
    return (row, config, executor, sha(cursor_raw))

def collect():
    global STAGE
    STAGE = 'START'
    need(sys.platform == 'linux' and (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) == (0, 0, 0, 0) and (os.uname().nodename == CFG['identity_33']))
    reader = Reader()
    try:
        STAGE = 'JOURNAL_SNAPSHOT'
        reader.lock(BASE + '/issuers/cycle.lock')
        reader.lock(BASE + '/cycles/cycle.lock')
        need(reader.names(ISSUER) == {'policy.json', '0000'} and reader.names(ISSUER + '/0000') == {'intent.json'})
        need(reader.names(CYCLE) == {'intent.json', 'prepare-intent.json', 'contain-intent.json', 'contain-done.json', 'incident.json'})
        need(reader.names(SCOPE) == {'plan.json', 'baseline.json', 'before.json', 'prepare.json', 'contain-intent.json', 'contained.json', 'runtime-package.json', 'wheels.tar'})
        policy = parse(pinned(reader, ISSUER + '/policy.json', 'policy.json'))
        issuer = parse(pinned(reader, ISSUER + '/0000/intent.json', 'issuer-intent'))
        cycle = parse(pinned(reader, CYCLE + '/intent.json', 'cycle-intent'))
        prepare = parse(pinned(reader, SCOPE + '/prepare.json', 'prepare.json'))
        plan = parse(pinned(reader, SCOPE + '/plan.json', 'plan.json'), True)
        baseline = parse(pinned(reader, SCOPE + '/baseline.json', 'baseline.json'))
        for path, key in ((CYCLE + '/prepare-intent.json', 'prepare.json'), (CYCLE + '/incident.json', 'incident.json'), (CYCLE + '/contain-intent.json', 'cycle-contain-intent'), (CYCLE + '/contain-done.json', 'contained.json'), (SCOPE + '/contain-intent.json', 'driver-contain-intent'), (SCOPE + '/contained.json', 'contained.json'), (SCOPE + '/before.json', 'before.json'), (SCOPE + '/runtime-package.json', 'runtime-package.json'), (SCOPE + '/wheels.tar', 'wheels.tar')):
            pinned(reader, path, key)
        STAGE = 'POLICY_BINDINGS'
        previous = bindings(policy, issuer, cycle, prepare, plan, baseline)
        STAGE = 'PREDECESSOR_LOCAL'
        complete, receipt, terminal = predecessor(reader, previous, prepare)
        STAGE = 'LEGACY_HOLD'
        legacy = legacy_hold(reader, baseline)
        STAGE = 'NATIVE_HOLD'
        native = native_hold(reader, complete, receipt, terminal)
        STAGE = 'STABLE_READBACK'
        reader.recheck()
        need(legacy_hold(reader, baseline) == legacy and native_hold(reader, complete, receipt, terminal) == native)
        reader.recheck()
        return dict(kind=CFG['identity_35'], collected_at_ns=time.time_ns(), host=CFG['identity_33'], policy_sha256=POLICY, plan_sha256=PLAN, historical_controller_source=policy['source'], controller_sha256=policy['accepted_controller_sha256'], runtime_sha256=policy['accepted_runtime_sha256'], predecessor=previous, original_expected_head_sha=plan['expected_head_sha'], cursor_sha256=native[3], checks=dict(policy_prepare_derivation=True, plan_read_only_scope=True, baseline_binding=True, predecessor_local_binding=True, history_0_through_2=True, native_hold=True, legacy_hold_continuity=True, protected_files_unchanged=True, no_sequence3_feed=True, no_executor_process=True, stable_readback=True), live_verified=False, db_verified=False, provider_verified=False, issue_allowed=False, replay_allowed=False)
    finally:
        reader.close()

def main():
    try:
        need(not sys.argv[1:])
        supplied = globals().get('EXPECTED_PINS')
        need(type(supplied) is dict and set(supplied) == set(PRIVATE_PIN_KEYS) and all((hexid(v) for v in supplied.values())))
        PINS.update(supplied)
        signal.signal(signal.SIGALRM, lambda *_: need(False))
        signal.alarm(25)
        result = collect()
        signal.alarm(0)
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except BaseException:
        print(json.dumps(dict(kind=CFG['identity_35'], state='REFUSED', stage=STAGE if STAGE in STAGES else 'START', issue_allowed=False), sort_keys=True, separators=(',', ':')))
        return 2
if __name__ == '__main__':
    sys.exit(main())
