"""Root-installed finite host authority; never fabricates a GitHub run binding.

Installation is a separate owner operation. This module only reads its accepted
bytes and actual Linux process identity. It does not install, renew, admit work,
store credentials, change a service, or repair retained issuer history.
"""
import base64
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

ROOT = Path('/var/lib/bridge-light-native-service')
UNIT = 'bridge-light-native-supervisor.service'
HOST = 'autopilot-lite-vnic'
OWNER = 'olegmed1-art'
REPOSITORY = OWNER + '/bridge-video-free'
RETAINED_SOURCE = 'f82d58efabf21ba62fd242a4fa02a8e7cfda1d23'
FALSE_FLAGS = ('repository_mutation', 'production_mutation', 'canon_mutation',
               'neon_mutation', 'server_mutation', 'external_mutation',
               'media_execution', 'paid_action', 'merge', 'deploy')
COVERAGE = ['direct_owner_sql', 'host_administration', 'workflow_administration',
            'workflow_reruns', 'main_pushes']


class Refusal(RuntimeError):
    """Fixed public code only; never propagate provider/credential exceptions."""


def require(ok, code='NATIVE_SERVICE_AUTHORITY'):
    if not ok:
        raise Refusal(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value, length=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{'+str(length)+'}', value) is not None


def encoded(value, *, ensure_ascii=False):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=ensure_ascii, allow_nan=False).encode()


def parse(raw, limit=262144, *, ensure_ascii=False):
    require(type(raw) is bytes and 0 < len(raw) <= limit, 'NATIVE_SERVICE_RECORD')
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'NATIVE_SERVICE_RECORD')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, 'NATIVE_SERVICE_RECORD'))
        require(encoded(value, ensure_ascii=ensure_ascii) == raw, 'NATIVE_SERVICE_RECORD')
        return value
    except (ValueError, TypeError, UnicodeError):
        raise Refusal('NATIVE_SERVICE_RECORD') from None


def timestamp(value):
    require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value),
            'NATIVE_SERVICE_TIME')
    try:
        return int(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        raise Refusal('NATIVE_SERVICE_TIME') from None


def exact(value, names):
    require(type(value) is dict and set(value) == set(names), 'NATIVE_SERVICE_SCHEMA')


def predecessor(value):
    exact(value, ('plan_sha256', 'terminal_sha256', 'sequence'))
    require(digest(value['plan_sha256']) and digest(value['terminal_sha256'])
            and type(value['sequence']) is int and 0 <= value['sequence'] < 9999,
            'NATIVE_SERVICE_PREDECESSOR')
    return value


@dataclass(frozen=True)
class Manifest:
    version: int
    kind: str
    source: str
    controller_sha256: str
    runtime_sha256: str
    policy_sha256: str
    unit_sha256: str
    python_sha256: str
    authority_sha256: str
    supervisor_sha256: str
    service_unit_sha256: str
    launcher_sha256: str
    argv: tuple
    not_before: str
    expires_at: str

    @classmethod
    def load(cls, raw):
        value = parse(raw, 16384)
        exact(value, (f.name for f in fields(cls)))
        require(type(value['version']) is int and value['version'] == 2
                and value['kind'] == 'LIGHT_NATIVE_READ_ONLY_SERVICE'
                and digest(value['source'], 40)
                and all(digest(value[k]) for k in value if k.endswith('_sha256')),
                'NATIVE_SERVICE_MANIFEST')
        argv = value['argv']
        require(type(argv) is list and argv == ['/usr/bin/python3', '-I', '-S', '-B',
                                                ROOT.as_posix()+'/launcher.py'],
                'NATIVE_SERVICE_ARGV')
        require(0 < timestamp(value['expires_at'])-timestamp(value['not_before']) <= 86400,
                'NATIVE_SERVICE_DURATION')
        return cls(**{**value, 'argv': tuple(argv)})


@dataclass(frozen=True)
class PlanBinding:
    plan_sha256: str
    work_key: str
    target_pr: int
    head_sha: str


@dataclass(frozen=True)
class FinitePolicy:
    raw: bytes
    digest: str
    source: str
    start: int
    end: int
    plans: tuple
    predecessor_raw: bytes

    @property
    def predecessor(self):
        return parse(self.predecessor_raw)


def validate_policy(raw, manifest):
    """Additional host admission gate; the existing issuer/Plan checks still run.

    Read-only scope is checked here before any execution port is available. Full
    Plan/Agreement, retirement, DB, provider and predecessor algorithms remain in
    the existing issuer and its verified driver child.
    """
    require(sha(raw) == manifest.policy_sha256, 'NATIVE_SERVICE_POLICY_BINDING')
    value = parse(raw, 196608)
    keys = {'version', 'action', 'source', 'accepted_controller_sha256',
            'accepted_runtime_sha256', 'not_before', 'expires_at', 'predecessor', 'plans', 'authority'}
    require(type(value) is dict and type(value.get('version')) is int
            and ((value['version'] == 1 and set(value) == keys) or
                 (value['version'] == 2 and set(value) == keys | {'retirements', 'retirement_evidence'})),
            'NATIVE_SERVICE_POLICY')
    require(value['action'] == 'issue' and value['source'] == manifest.source
            and value['accepted_controller_sha256'] == manifest.controller_sha256
            and value['accepted_runtime_sha256'] == manifest.runtime_sha256,
            'NATIVE_SERVICE_POLICY_BINDING')
    authority = value['authority']
    exact(authority, ('owner', 'coverage', 'delegation', 'evidence'))
    require(authority['owner'] == OWNER and authority['coverage'] == COVERAGE
            and authority['delegation'] == 'FINITE_ISSUER_AGREEMENTS'
            and type(authority['evidence']) is str and 1 <= len(authority['evidence']) <= 1024,
            'NATIVE_SERVICE_DELEGATION')
    if value['version'] == 2:
        from ops.light_native_retirement import references, journal_pins
        refs = references(value['retirements'])
        require(bool(refs) and all(ref['policy_sha256'] != manifest.policy_sha256 for ref in refs),
                'NATIVE_SERVICE_RETIREMENTS')
        require(type(value['retirement_evidence']) is dict and
                set(value['retirement_evidence']) == {ref['policy_sha256'] for ref in refs},
                'NATIVE_SERVICE_RETIREMENTS')
        for pins in value['retirement_evidence'].values():
            journal_pins(pins)
    start, end = (timestamp(value[k]) for k in ('not_before', 'expires_at'))
    require(0 < end-start <= 86400 and start <= timestamp(manifest.not_before)
            < timestamp(manifest.expires_at) <= end, 'NATIVE_SERVICE_DURATION')
    previous = predecessor(value['predecessor'])
    require(type(value['plans']) is list and 1 <= len(value['plans']) <= 8
            and previous['sequence']+len(value['plans']) < 10000, 'NATIVE_SERVICE_CATALOGUE')
    bindings = []
    seen = (set(), set(), set())
    for entry in value['plans']:
        exact(entry, ('plan_base64', 'accepted_plan_sha256'))
        require(type(entry['plan_base64']) is str and digest(entry['accepted_plan_sha256']),
                'NATIVE_SERVICE_PLAN')
        try:
            plan_raw = base64.b64decode(entry['plan_base64'], validate=True)
        except ValueError:
            raise Refusal('NATIVE_SERVICE_PLAN') from None
        require(sha(plan_raw) == entry['accepted_plan_sha256'], 'NATIVE_SERVICE_PLAN')
        # Plan uses native_cli_permission_engine.encode, whose ASCII escaping
        # is part of the accepted bytes. Issuer policy itself remains UTF-8.
        plan = parse(plan_raw, 16384, ensure_ascii=True)
        exact(plan, ('version', 'source', 'repository', 'target_pr', 'expected_head_sha',
                     'work_key', 'objective', 'priority', 'task_spec_json', 'branch'))
        require(type(plan['version']) is int and plan['version'] == 1
                and plan['source'] == RETAINED_SOURCE and plan['repository'] == REPOSITORY
                and type(plan['target_pr']) is int and 0 < plan['target_pr'] <= 1000000
                and digest(plan['expected_head_sha'], 40)
                and type(plan['work_key']) is str and
                re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:-]{0,139}', plan['work_key']),
                'NATIVE_SERVICE_PLAN')
        spec = plan['task_spec_json']
        base_keys = {'assignment_schema', 'repository', 'target_pr', 'expected_head_sha',
                     'execution_mode', 'exact_head_binding', 'cost_cap_microusd', 'max_repair_attempts', *FALSE_FLAGS}
        focus_keys = {'focus_path', 'focus_paths', 'required_checks', 'preserve',
                      'verification_kind', 'expected_changed_files'}
        require(type(spec) is dict and base_keys <= set(spec) <= base_keys | focus_keys
                and spec['assignment_schema'] == 'SLAVIK_DISPATCH_ASSIGNMENT_V1'
                and all(spec[k] == plan[k] for k in ('repository', 'target_pr', 'expected_head_sha'))
                and spec['execution_mode'] == 'READ_ONLY' and spec['exact_head_binding'] is True
                and all(spec[k] is False for k in FALSE_FLAGS)
                and all(type(spec[k]) is int and spec[k] == 0 for k in ('cost_cap_microusd', 'max_repair_attempts')),
                'NATIVE_SERVICE_READ_ONLY')
        identity = (entry['accepted_plan_sha256'], plan['work_key'], plan['target_pr'])
        require(identity[0] != previous['plan_sha256'], 'NATIVE_SERVICE_PREDECESSOR')
        for identities, item in zip(seen, identity):
            require(item not in identities, 'NATIVE_SERVICE_DUPLICATE')
            identities.add(item)
        bindings.append(PlanBinding(*identity, plan['expected_head_sha']))
    return FinitePolicy(raw, sha(raw), manifest.source, start, end, tuple(bindings), encoded(previous))


def root_directory(path, *, private=False):
    require(os.name == 'posix' and os.geteuid() == 0, 'NATIVE_SERVICE_LINUX_ROOT')
    path = Path(path)
    require(path.is_absolute(), 'NATIVE_SERVICE_PATH')
    for parent in (path, *path.parents):
        row = parent.lstat()
        require(stat.S_ISDIR(row.st_mode) and row.st_uid == 0 and not row.st_mode & 0o022,
                'NATIVE_SERVICE_PARENT')
    if private:
        require(stat.S_IMODE(path.lstat().st_mode) == 0o700, 'NATIVE_SERVICE_PRIVATE_ROOT')


def metadata_signature(row):
    """stat_result tuple equality omits ns precision and other extra fields."""
    names = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size',
             'st_atime_ns', 'st_mtime_ns', 'st_ctime_ns')
    return tuple(getattr(row, name) for name in names) + (getattr(row, 'st_rdev', None),)


def protected_read(path, expected=None, *, limit=4*1024*1024, mode=0o600):
    path = Path(path)
    root_directory(path.parent)
    # Full stat readback intentionally includes atime. On a relatime mount an
    # ordinary read could change it and falsely look like concurrent drift.
    # Root-owned evidence must support O_NOATIME; never retry without the flag.
    no_atime = getattr(os, 'O_NOATIME', None)
    require(type(no_atime) is int and no_atime > 0, 'NATIVE_SERVICE_NOATIME_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | no_atime)
    try:
        before = os.fstat(fd)
        signature = metadata_signature(before)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == mode and 0 < before.st_size <= limit,
                'NATIVE_SERVICE_FILE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit+1)
        require(len(raw) <= limit and metadata_signature(os.fstat(fd)) == signature
                and metadata_signature(path.stat(follow_symlinks=False)) == signature,
                'NATIVE_SERVICE_FILE_CHANGED')
        require(expected is None or digest(expected) and sha(raw) == expected, 'NATIVE_SERVICE_BINDING')
        return raw
    finally:
        os.close(fd)


def proc_identity(pid):
    """Read real Linux boot/start/exe/argv/cgroup; reusable by a driver child."""
    require(os.name == 'posix' and type(pid) is int and pid > 0, 'NATIVE_SERVICE_PROCESS')
    path = Path('/proc') / str(pid)
    before = (path/'stat').read_text()
    start = int(before.rsplit(')', 1)[1].split()[19])
    argv_raw = (path/'cmdline').read_bytes()
    require(argv_raw.endswith(b'\0') and len(argv_raw) <= 65536, 'NATIVE_SERVICE_PROCESS')
    argv = tuple(x.decode('utf-8') for x in argv_raw[:-1].split(b'\0'))
    with (path/'exe').open('rb') as stream:
        binary = stream.read(64*1024*1024+1)
    require(len(binary) <= 64*1024*1024 and (path/'stat').read_text().rsplit(')', 1)[1].split()[19] == str(start),
            'NATIVE_SERVICE_PROCESS_CHANGED')
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    require(re.fullmatch('[0-9a-f-]{36}', boot) and start > 0, 'NATIVE_SERVICE_PROCESS')
    return dict(pid=pid, start_ticks=start, boot_id=boot, exe_sha256=sha(binary),
                argv=argv, cgroup=(path/'cgroup').read_text().strip())


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_ticks: int
    boot_id: str
    exe_sha256: str
    argv: tuple
    cgroup: str
    invocation_id: str


_UNIT_HELPERS = {}


def _unit_helper(manifest):
    """Execute only same-directory protected bytes after their manifest pin passes.

    A pre-imported ops helper is not provenance. The private module is built
    from these exact bytes, not found through sys.path/import hooks or trusted
    because a loaded systemd property happens to match.
    """
    expected = manifest.service_unit_sha256
    require(digest(expected), 'NATIVE_SERVICE_UNIT_HELPER')
    path = Path(__file__).with_name('light_native_service_unit.py')
    raw = protected_read(path, expected, mode=0o600, limit=65536)
    require(sha(raw) == expected, 'NATIVE_SERVICE_UNIT_HELPER')
    key = (str(path), expected)
    if key not in _UNIT_HELPERS:
        name = '_bridge_native_unit_'+sha(str(path).encode()+raw)
        module = types.ModuleType(name)
        module.__file__ = str(path)
        sys.modules[name] = module
        try:
            exec(compile(raw, str(path), 'exec'), module.__dict__)
        except BaseException:
            sys.modules.pop(name, None)
            raise Refusal('NATIVE_SERVICE_UNIT_HELPER') from None
        _UNIT_HELPERS[key] = module
    return _UNIT_HELPERS[key]


def host_identity(pid, manifest):
    try:
        require(type(manifest.version) is int and manifest.version == 2,
                'NATIVE_SERVICE_MANIFEST')
        helper = _unit_helper(manifest)
        require(helper.ROOT == ROOT and helper.UNIT == UNIT and helper.ARGV == manifest.argv,
                'NATIVE_SERVICE_UNIT_HELPER')
        binding = helper.collect_loaded(pid, expected_python_sha256=manifest.python_sha256,
                                         expected_unit_sha256=manifest.unit_sha256)
        require(binding.unit_sha256 == manifest.unit_sha256, 'NATIVE_SERVICE_UNIT')
        row = binding.process
        return ProcessIdentity(row.pid, row.start_ticks, row.boot_id, row.exe_sha256,
                               row.argv, row.cgroup, row.invocation_id)
    except Exception:
        raise Refusal('NATIVE_SERVICE_UNIT') from None


@dataclass(frozen=True)
class ChildContext:
    version: int
    kind: str
    manifest_sha256: str
    installation_sha256: str
    policy_sha256: str
    source: str
    controller_sha256: str
    runtime_sha256: str
    host: str
    unit: str
    process: ProcessIdentity
    not_before: int
    expires_at: int
    monotonic_start_ns: int
    monotonic_end_ns: int
    purpose: str
    index: int | None
    plan_sha256: str | None
    predecessor_sha256: str | None
    intent_binding_sha256: str | None

    def wire(self):
        return encoded(asdict(self))

    @classmethod
    def load(cls, raw):
        value = parse(raw, 16384)
        exact(value, (f.name for f in fields(cls)))
        process = value['process']
        exact(process, (f.name for f in fields(ProcessIdentity)))
        require(type(value['version']) is int and value['version'] == 1
                and value['kind'] == 'ROOT_ACCEPTED_HOST_SERVICE'
                and value['host'] == HOST and value['unit'] == UNIT
                and digest(value['source'], 40)
                and all(digest(value[k]) for k in ('manifest_sha256', 'installation_sha256',
                    'policy_sha256', 'controller_sha256', 'runtime_sha256'))
                and all(type(value[k]) is int for k in ('not_before', 'expires_at', 'monotonic_start_ns', 'monotonic_end_ns'))
                and 0 < value['expires_at']-value['not_before'] <= 1800
                and 0 < value['monotonic_end_ns']-value['monotonic_start_ns'] <= 1800*10**9,
                'NATIVE_SERVICE_CHILD_CONTEXT')
        require(value['purpose'] in ('OBSERVE', 'ISSUE'), 'NATIVE_SERVICE_CHILD_PURPOSE')
        if value['purpose'] == 'OBSERVE':
            require(all(value[k] is None for k in ('index', 'plan_sha256', 'predecessor_sha256', 'intent_binding_sha256')),
                    'NATIVE_SERVICE_CHILD_PURPOSE')
        else:
            require(type(value['index']) is int and 0 <= value['index'] < 8
                    and all(digest(value[k]) for k in ('plan_sha256', 'predecessor_sha256', 'intent_binding_sha256')),
                    'NATIVE_SERVICE_CHILD_PURPOSE')
        require(type(process['pid']) is int and process['pid'] > 0
                and type(process['start_ticks']) is int and process['start_ticks'] > 0
                and type(process['boot_id']) is str and re.fullmatch('[0-9a-f-]{36}', process['boot_id'])
                and digest(process['exe_sha256']) and digest(process['invocation_id'], 32)
                and process['cgroup'] == '0::/system.slice/'+UNIT
                and type(process['argv']) is list and all(type(x) is str for x in process['argv']),
                'NATIVE_SERVICE_CHILD_CONTEXT')
        return cls(**{**value, 'process': ProcessIdentity(**{**process, 'argv': tuple(process['argv'])})})


def _current_main(source):
    # Reuse the existing fresh primary GitHub main check, without inventing a run.
    from ops.light_native_pilot_release import staging
    staging.require_current_main(source)


def _packages(controller, runtime, manifest):
    from ops import light_native_lane_controller as owner
    owner.validate_package(controller, manifest.source, manifest.controller_sha256)
    # The retained release.package/bundle codec is historical default ASCII;
    # never normalize its bytes to the service's UTF-8 journal codec.
    retained = parse(runtime, 3*1024*1024, ensure_ascii=True)
    require(retained.get('source') == RETAINED_SOURCE, 'NATIVE_SERVICE_RUNTIME')
    owner.release.validate(retained['runtime'], retained['source'], retained['runtime']['sha256'])


def _descendant(pid):
    current = os.getpid()
    for _ in range(64):
        if current == pid:
            return True
        if current <= 1:
            return False
        current = int(Path('/proc', str(current), 'stat').read_text().rsplit(')', 1)[1].split()[1])
    return False


def _retain_context(root, raw):
    """Retain a derived finite capability, never a new owner acceptance."""
    root_directory(root, private=True)
    directory = root/'contexts'
    if not directory.exists() and not directory.is_symlink():
        directory.mkdir(mode=0o700)
        _fsync(root)
    root_directory(directory, private=True)
    path = directory/(sha(raw)+'.json')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        # Coarse monotonic clocks can produce identical contexts. Reading the
        # original exact bytes neither extends the deadline nor replaces them.
        require(protected_read(path, sha(raw)) == raw, 'NATIVE_SERVICE_CONTEXT_READBACK')
        return
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync(directory)
    require(protected_read(path, sha(raw)) == raw, 'NATIVE_SERVICE_CONTEXT_READBACK')


def _read_context(root, raw):
    try:
        require(protected_read(root/'contexts'/(sha(raw)+'.json'), sha(raw)) == raw,
                'NATIVE_SERVICE_CHILD_NOT_RETAINED')
    except OSError:
        raise Refusal('NATIVE_SERVICE_CHILD_NOT_RETAINED') from None


def _fsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def issue_binding(value, host):
    exact(value, ('version', 'kind', 'manifest_sha256', 'policy_sha256', 'index',
                  'plan_sha256', 'predecessor', 'observation_sha256'))
    previous = predecessor(value['predecessor'])
    index = value['index']
    require(type(value['version']) is int and value['version'] == 1
            and value['kind'] == 'LIGHT_NATIVE_SERVICE_ISSUE_INTENT'
            and value['manifest_sha256'] == host.accepted and value['policy_sha256'] == host.policy.digest
            and type(index) is int and 0 <= index < len(host.policy.plans)
            and value['plan_sha256'] == host.policy.plans[index].plan_sha256
            and digest(value['observation_sha256'])
            and previous['sequence'] == host.policy.predecessor['sequence']+index
            and (previous == host.policy.predecessor if index == 0 else
                 previous['plan_sha256'] == host.policy.plans[index-1].plan_sha256),
            'NATIVE_SERVICE_ISSUE_BINDING')
    return value


class HostAuthority:
    """Concrete guard for issuer/driver boundaries, pinned to one real invocation.

    A caller digest is only a selector: installation.json must independently bind
    that exact accepted manifest in a root-owned 0700 installation. No method
    creates acceptance, extends the window, reinterprets UNKNOWN or modifies HOLD.
    """
    def __init__(self, accepted_manifest_sha256, *, root=ROOT, child_context=None):
        self.root = Path(root)
        self.accepted = accepted_manifest_sha256
        require(digest(self.accepted), 'NATIVE_SERVICE_NOT_ACCEPTED')
        root_directory(self.root, private=True)
        self.raw = protected_read(self.root/'manifest.json', self.accepted, limit=16384)
        self.manifest = Manifest.load(self.raw)
        self.installation = protected_read(self.root/'installation.json', limit=16384)
        install = parse(self.installation)
        exact(install, ('version', 'kind', 'owner', 'manifest_sha256', 'source', 'controller_sha256',
                        'runtime_sha256', 'policy_sha256', 'unit_sha256', 'accepted_at', 'evidence_sha256',
                        'accepted_boot_id', 'accepted_wall_ns', 'accepted_monotonic_ns'))
        require(type(install['version']) is int and install['version'] == 1
                and install['kind'] == 'ROOT_ACCEPTED_SERVICE_INSTALLATION' and install['owner'] == OWNER
                and install['manifest_sha256'] == self.accepted and digest(install['evidence_sha256'])
                and timestamp(install['accepted_at']) <= time.time()
                and all(install[k] == getattr(self.manifest, k) for k in
                        ('source', 'controller_sha256', 'runtime_sha256', 'policy_sha256', 'unit_sha256')),
                'NATIVE_SERVICE_INSTALLATION')
        require(type(install['accepted_boot_id']) is str and
                re.fullmatch('[0-9a-f-]{36}', install['accepted_boot_id'])
                and type(install['accepted_wall_ns']) is int and type(install['accepted_monotonic_ns']) is int
                and install['accepted_monotonic_ns'] > 0
                and install['accepted_wall_ns']//10**9 == timestamp(install['accepted_at'])
                and timestamp(self.manifest.not_before)*10**9 <= install['accepted_wall_ns']
                < timestamp(self.manifest.expires_at)*10**9, 'NATIVE_SERVICE_INSTALLATION_ANCHOR')
        self.anchor_wall_ns = install['accepted_wall_ns']
        self.anchor_monotonic_ns = install['accepted_monotonic_ns']
        self.anchor_boot_id = install['accepted_boot_id']
        self.monotonic_deadline_ns = (self.anchor_monotonic_ns +
            timestamp(self.manifest.expires_at)*10**9-self.anchor_wall_ns)
        policy_raw = protected_read(self.root/'policy.json', self.manifest.policy_sha256, limit=196608)
        self.policy = validate_policy(policy_raw, self.manifest)
        self.source = self.manifest.source
        self.child = None if child_context is None else ChildContext.load(child_context)
        self.process = host_identity(os.getpid() if self.child is None else self.child.process.pid, self.manifest)
        require(self.process.boot_id == self.anchor_boot_id, 'NATIVE_SERVICE_BOOT_CHANGED')
        if self.child is not None:
            _read_context(self.root, child_context)
            require(all(getattr(self.child, k) == expected for k, expected in {
                'manifest_sha256': self.accepted, 'installation_sha256': sha(self.installation),
                'policy_sha256': self.policy.digest, 'source': self.source,
                'controller_sha256': self.manifest.controller_sha256,
                'runtime_sha256': self.manifest.runtime_sha256, 'process': self.process}.items())
                and self.child.not_before*10**9 >= self.anchor_wall_ns-self.anchor_wall_ns % 10**9
                and self.child.expires_at <= timestamp(self.manifest.expires_at)
                and self.child.monotonic_start_ns >= self.anchor_monotonic_ns
                and self.child.monotonic_end_ns <= self.monotonic_deadline_ns
                and _descendant(self.process.pid), 'NATIVE_SERVICE_CHILD_BINDING')
            own = proc_identity(os.getpid())
            require(own['cgroup'] == self.process.cgroup and own['exe_sha256'] == self.manifest.python_sha256,
                    'NATIVE_SERVICE_CHILD_BINDING')
        self.failed = False
        self.last_wall = time.time()
        self.assert_current()

    def _context(self, start, end, mono_start, mono_end, purpose, binding):
        return ChildContext(1, 'ROOT_ACCEPTED_HOST_SERVICE', self.accepted, sha(self.installation),
            self.policy.digest, self.source, self.manifest.controller_sha256, self.manifest.runtime_sha256,
            HOST, UNIT, self.process, start, end, mono_start, mono_end, purpose,
            None if binding is None else binding['index'], None if binding is None else binding['plan_sha256'],
            None if binding is None else sha(encoded(binding['predecessor'])),
            None if binding is None else sha(encoded(binding)))

    def _immutable(self):
        root_directory(self.root, private=True)
        require(not (self.root/'STOP').exists() and not (self.root/'STOP').is_symlink(), 'NATIVE_SERVICE_STOP')
        require(protected_read(self.root/'manifest.json', self.accepted) == self.raw
                and protected_read(self.root/'installation.json', sha(self.installation)) == self.installation
                and protected_read(self.root/'policy.json', self.policy.digest) == self.policy.raw,
                'NATIVE_SERVICE_INSTALLATION_CHANGED')
        controller = protected_read(self.root/'controller.json', self.manifest.controller_sha256)
        runtime = protected_read(self.root/'runtime.json', self.manifest.runtime_sha256)
        _packages(controller, runtime, self.manifest)
        protected_read(Path(__file__), self.manifest.authority_sha256, mode=0o600)
        protected_read(Path(__file__).with_name('light_native_service_supervisor.py'),
                       self.manifest.supervisor_sha256, mode=0o600)
        protected_read(Path(__file__).with_name('light_native_service_unit.py'),
                       self.manifest.service_unit_sha256, mode=0o600)
        protected_read(self.root/'launcher.py', self.manifest.launcher_sha256, mode=0o600)
        if self.child is not None:
            raw = self.child.wire()
            _read_context(self.root, raw)
            if self.child.purpose == 'ISSUE':
                intent = parse(protected_read(self.root/'journal'/f'{self.child.index:04d}'/'intent.json', limit=32768))
                require(intent.get('context') == parse(raw), 'NATIVE_SERVICE_ISSUE_BINDING')
                binding = {k: v for k, v in intent.items() if k != 'context'}
                issue_binding(binding, self)
                require(sha(encoded(binding)) == self.child.intent_binding_sha256
                        and binding['index'] == self.child.index
                        and binding['plan_sha256'] == self.child.plan_sha256
                        and sha(encoded(binding['predecessor'])) == self.child.predecessor_sha256,
                        'NATIVE_SERVICE_ISSUE_BINDING')

    def assert_current(self):
        require(not self.failed, 'NATIVE_SERVICE_AUTHORITY_CLOSED')
        try:
            now = time.time()
            require(timestamp(self.manifest.not_before) <= now < timestamp(self.manifest.expires_at)
                    and now >= self.last_wall and now*10**9 >= self.anchor_wall_ns
                    and self.anchor_monotonic_ns <= time.monotonic_ns() < self.monotonic_deadline_ns,
                    'NATIVE_SERVICE_EXPIRED')
            if self.child is not None:
                require(self.child.not_before <= now < self.child.expires_at
                        and self.child.monotonic_start_ns <= time.monotonic_ns() < self.child.monotonic_end_ns,
                        'NATIVE_SERVICE_CHILD_EXPIRED')
            self._immutable()
            require(os.uname().nodename == HOST and host_identity(self.process.pid, self.manifest) == self.process,
                    'NATIVE_SERVICE_PROCESS_CHANGED')
            _current_main(self.source)
            self._immutable()
            require(host_identity(self.process.pid, self.manifest) == self.process,
                    'NATIVE_SERVICE_PROCESS_CHANGED')
            checked = time.time()
            require(now <= checked < timestamp(self.manifest.expires_at)
                    and self.anchor_monotonic_ns <= time.monotonic_ns() < self.monotonic_deadline_ns,
                    'NATIVE_SERVICE_EXPIRED')
            if self.child is not None:
                require(checked < self.child.expires_at and time.monotonic_ns() < self.child.monotonic_end_ns,
                        'NATIVE_SERVICE_CHILD_EXPIRED')
            if self.child is not None:
                require(_descendant(self.process.pid), 'NATIVE_SERVICE_CHILD_BINDING')
            self.last_wall = checked
        except BaseException:
            self.failed = True
            raise Refusal('NATIVE_SERVICE_AUTHORITY_CLOSED') from None

    assert_running = assert_current

    def child_context(self, seconds=1800, *, purpose='OBSERVE', binding=None):
        require(self.child is None and type(seconds) is int and 1 <= seconds <= 1800,
                'NATIVE_SERVICE_CHILD_DURATION')
        self.assert_current()
        require(purpose in ('OBSERVE', 'ISSUE') and (binding is None if purpose == 'OBSERVE' else binding is not None),
                'NATIVE_SERVICE_CHILD_PURPOSE')
        if binding is not None:
            binding = issue_binding(parse(encoded(binding)), self)
        start = int(time.time())
        end = min(start+seconds, timestamp(self.manifest.expires_at), self.policy.end)
        require(end > start, 'NATIVE_SERVICE_EXPIRED')
        monotonic = time.monotonic_ns()
        context = self._context(start, end, monotonic,
            min(monotonic+(end-start)*10**9, self.monotonic_deadline_ns), purpose, binding)
        _retain_context(self.root, context.wire())
        return context

    def assert_issue(self, policy_sha256, index, plan_sha256):
        """Mandatory integration check before issuer/driver effectful entry."""
        self.assert_current()
        require(self.child is not None and self.child.purpose == 'ISSUE'
                and policy_sha256 == self.policy.digest and index == self.child.index
                and type(index) is int and plan_sha256 == self.child.plan_sha256,
                'NATIVE_SERVICE_CHILD_PURPOSE')
