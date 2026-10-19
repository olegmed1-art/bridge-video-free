"""Disposable hosted-Ubuntu/PID1 proof of ExecutionSession, never a launcher.

CI stages only fixed hostname/path/unit selectors into the execution module.
The actual collector, /proc reader, session algorithms and timer checks remain
unchanged. Requests, prior HOLD data and upstream run authority are explicitly
synthetic; no GitHub authentication, legacy attestation, production restore,
database, provider or workload execution is claimed or performed.

Two startup cases add a fixed CI-only ExecStartPost gate, and use --no-block
only to let the parent release that gate. No production source or accepted
unit profile acquires this property. Every worker enters through the real
production readiness code, without a fixture wait for ActiveState=active.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


PREFIX = 'bridge-native-execution-session-fixture-'
PURPOSE = b'CI_ONLY_EXECUTION_SESSION_NO_WORK_AUTHORITY\n'
CASES = ('first', 'restart', 'foreign', 'python', 'inputs', 'unit', 'expiry', 'uncertain')
COLLECTORS = ('_execution_identity', '_await_supervisor', '_session_process', '_session_exists',
              '_session_show', '_session_exec', '_supervisor_timer',
              '_session_read', '_clock_shape', '_boot_id', 'ExecutionSession')


def need(ok, code):
    if not ok:
        raise RuntimeError('EXECUTION_SESSION_FIXTURE_' + code)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def command(*argv, timeout=10, allow_failure=False, inherit_invocation=False):
    environment = {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C'}
    if inherit_invocation:
        invocation = os.environ.get('INVOCATION_ID', '')
        need(re.fullmatch('[0-9a-f]{32}', invocation), 'INHERITED_INVOCATION')
        environment['INVOCATION_ID'] = invocation
    result = subprocess.run(argv, capture_output=True, timeout=timeout, env=environment)
    need(len(result.stdout) <= 65536 and len(result.stderr) <= 65536, 'COMMAND_OUTPUT_BOUND')
    need(allow_failure or result.returncode == 0, 'COMMAND_REFUSED')
    return result


def new_file(path, raw, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def metadata(row):
    return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_gid, row.st_nlink,
            row.st_size, row.st_atime_ns, row.st_mtime_ns, row.st_ctime_ns)


def root_file(path, limit=262144):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
             stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1 and
             0 < before.st_size <= limit, 'PROTECTED_FILE')
        raw = os.read(fd, limit + 1)
        need(len(raw) == before.st_size and metadata(before) == metadata(os.fstat(fd)) ==
             metadata(path.stat(follow_symlinks=False)), 'PROTECTED_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def replace_owned(path, raw):
    before = path.stat(follow_symlinks=False)
    need(stat.S_ISREG(before.st_mode) and before.st_uid == before.st_gid == 0 and
         stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1, 'REPLACE_SCOPE')
    temporary = path.with_name(path.name + '.fixture-new')
    new_file(temporary, raw)
    need((path.stat(follow_symlinks=False).st_dev, path.stat(follow_symlinks=False).st_ino) ==
         (before.st_dev, before.st_ino), 'REPLACE_CHANGED')
    os.replace(temporary, path)


def unit_name(root, label):
    need(root.parent == Path('/run') and re.fullmatch(PREFIX + '[a-z0-9_]{8,32}', root.name) and
         re.fullmatch('[a-z][a-z-]{0,35}', label), 'NAMESPACE')
    return root.name + '-' + label + '.service'


def show(unit, keys):
    need(re.fullmatch(PREFIX + r'[a-z0-9_]{8,32}-[a-z-]{1,36}\.service', unit), 'UNIT_SCOPE')
    output = command('/usr/bin/systemctl', '--no-pager', 'show', '--all', unit,
                     *['--property=' + name for name in keys]).stdout.decode('ascii')
    rows = {}
    for line in output.splitlines():
        key, sep, value = line.partition('=')
        need(sep and key in keys and key not in rows, 'SHOW_FIELDS')
        rows[key] = value
    need(set(rows) == set(keys), 'SHOW_FIELDS')
    return rows


def empty(unit):
    row = show(unit, ('MainPID', 'ControlPID', 'ControlGroup'))
    need(row['ControlGroup'] in ('', '/system.slice/' + unit), 'CGROUP_SCOPE')
    path = Path('/sys/fs/cgroup/system.slice') / unit / 'cgroup.events'
    populated = dict(line.split() for line in path.read_text().splitlines()).get('populated') if path.exists() else '0'
    return row['MainPID'] == row['ControlPID'] == '0' and populated == '0'


def wait_for(call, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = call()
        if value:
            return value
        time.sleep(.025)
    raise RuntimeError('EXECUTION_SESSION_FIXTURE_WAIT_BOUND')


def receipt(directory, prefix, *, excluding=()):
    paths = sorted(path for path in directory.glob(prefix + '-*.json') if path.name not in excluding)
    need(len(paths) <= 1, 'AMBIGUOUS_RECEIPT')
    return (paths[0], json.loads(root_file(paths[0]))) if paths else None


class Layout:
    def __init__(self, root, label):
        self.root, self.label = root, label
        self.unit = unit_name(root, label)
        self.case = root / label
        self.execution = self.case / 'execution'
        self.owner = self.case / 'owner'
        self.stage = self.case / 'source'
        self.directory = None
        self.digest = None
        self.unit_intended = False
        self.variant = None
        self.barrier = None
        self.barrier_metadata = None

    def prepare(self, repo, source, *, variant='normal', window=45):
        need(variant in ('normal', 'wrong-python', 'partial', 'orphan-first', 'lost-ack', 'timer',
                         'startup-release', 'startup-timeout'), 'FIXED_VARIANT')
        self.variant = variant
        need(show(self.unit, ('LoadState',))['LoadState'] == 'not-found', 'PREEXISTING_UNIT')
        self.case.mkdir(mode=0o700)
        for path in (self.execution, self.owner, self.stage):
            path.mkdir(mode=0o700)
        (self.stage / 'ops').mkdir(mode=0o700)
        original = (repo / 'ops/light_native_lane_execution.py').read_bytes()
        text = original.decode('utf-8')
        substitutions = {
            "ROOT = Path('/var/lib/bridge-light-native-lane-execution')": 'ROOT = Path(' + repr(str(self.execution)) + ')',
            "OWNER_ROOT = Path('/var/lib/bridge-light-native-lane-owner')": 'OWNER_ROOT = Path(' + repr(str(self.owner)) + ')',
            "SUPERVISOR = 'bridge-light-native-lane-supervisor.service'": 'SUPERVISOR = ' + repr(self.unit),
        }
        for before, after in substitutions.items():
            need(text.count(before) == 1, 'STAGE_SELECTOR_CHANGED')
            text = text.replace(before, after)
        need(text.count("'autopilot-lite-vnic'") == 2, 'HOST_SELECTOR_CHANGED')
        text = text.replace("'autopilot-lite-vnic'", repr(os.uname().nodename))
        staged = text.encode('utf-8')
        trees = [ast.parse(raw.decode('utf-8')) for raw in (original, staged)]
        for name in COLLECTORS:
            nodes = [next(node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name == name)
                     for tree in trees]
            need(ast.dump(nodes[0], include_attributes=False) == ast.dump(nodes[1], include_attributes=False),
                 'COLLECTOR_CHANGED')
        files = sorted((repo / 'ops').glob('*.py'))
        need(len(files) <= 512 and sum(path.stat().st_size for path in files) <= 16 * 1024 * 1024, 'SOURCE_BOUND')
        for path in files:
            raw = staged if path.name == 'light_native_lane_execution.py' else path.read_bytes()
            need(len(raw) <= 1024 * 1024, 'SOURCE_FILE_BOUND')
            new_file(self.stage / 'ops' / path.name, raw)
        fixture_raw = Path(__file__).read_bytes()
        new_file(self.stage / 'fixture.py', fixture_raw)
        helper_sha = sha(fixture_raw)
        script = ("import hashlib,importlib.util,pathlib,sys\n"
            "p=pathlib.Path(" + repr(str(self.stage / 'fixture.py')) + ")\n"
            "assert hashlib.sha256(p.read_bytes()).hexdigest()==" + repr(helper_sha) + "\n"
            "s=importlib.util.spec_from_file_location('execution_session_ci_child',p)\n"
            "m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)\n"
            "m.worker(sys.argv[1],sys.argv[2],pathlib.Path(__file__).parent)\n").encode()
        plan_sha = sha(encoded(dict(synthetic_case=self.label, source=source)))
        context = encoded(dict(owner_plan_sha256=plan_sha, controller_sha256=sha(staged),
            wheels_sha256=sha(b'CI_ONLY_NO_DRIVER'), run_id=1, attempt=1))
        owner_scope = self.owner / plan_sha
        owner_scope.mkdir(mode=0o700)
        wall, monotonic = time.time_ns(), time.monotonic_ns()
        end = monotonic + window * 10**9
        python_sha = sha(Path('/usr/bin/python3').resolve(strict=True).read_bytes())
        value = dict(version=3, source='f82d58efabf21ba62fd242a4fa02a8e7cfda1d23',
            controller_source=source, seconds=45, expires_at=wall // 10**9 + 45,
            prior=dict(hostname='autopilot-lite-vnic', pid=101, invocation_id='a' * 32,
                release='/opt/bridge-school/school-autopilot-production-light/releases/' + 'b' * 40,
                fingerprint='c' * 64), protected={}, protected_sha256='d' * 64,
            cursor_sha256='e' * 64, dispatch_id='00000000-0000-4000-8000-000000000001',
            original=dict(ActiveState='active', SubState='running', MainPID='101',
                InvocationID='a' * 32, NRestarts='0'), supervisor_sha256=sha(script),
            owner_context_sha256=sha(context), sequence=0,
            clock=dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                wall_ns=wall, monotonic_start_ns=monotonic, monotonic_end_ns=end, run_guard_deadline_ns=end),
            python_sha256='0' * 64 if variant == 'wrong-python' else python_sha)
        # Avoid rounding past expires_at: the real request clock has the same bound.
        value['clock']['monotonic_end_ns'] = min(end, monotonic + value['expires_at'] * 10**9 - wall)
        raw = encoded(value)
        self.digest = sha(raw)
        self.directory = self.execution / self.digest
        self.directory.mkdir(mode=0o700)
        for name, data in (('request.json', raw), ('supervisor.py', script), ('owner-context.json', context)):
            new_file(self.directory / name, data)
        new_file(owner_scope / 'execution.json', encoded(dict(request_sha256=self.digest)))
        new_file(self.case / 'context.json', encoded(dict(version=1, purpose=PURPOSE.decode().strip(), label=self.label,
            unit=self.unit, source=source, variant=variant, root=str(self.root), directory=str(self.directory),
            digest=self.digest, original_source_sha256=sha(original), staged_source_sha256=sha(staged),
            collector_ast_unchanged=True, runtime_seconds=30)))
        if variant == 'partial':
            self.barrier = self.directory / 'session-intent.json'
        elif variant == 'orphan-first':
            self.barrier = self.directory / 'session-first.json'
        if self.barrier is not None:
            new_file(self.barrier, b'{')
            self.barrier_metadata = metadata(self.barrier.stat(follow_symlinks=False))
        return value

    def barrier_unchanged(self):
        return (self.barrier is not None and sha(root_file(self.barrier)) == sha(b'{') and
                metadata(self.barrier.stat(follow_symlinks=False)) == self.barrier_metadata)

    def launch(self):
        row = show(self.unit, ('LoadState', 'ActiveState'))
        if row['LoadState'] != 'not-found':
            need(row['ActiveState'] in ('inactive', 'failed'), 'RESTART_SCOPE')
            self.stop()
            wait_for(lambda: show(self.unit, ('LoadState',))['LoadState'] == 'not-found', 8)
        self.unit_intended = True
        hook = '/usr/bin/python3 -I -S -B ' + str(self.directory / 'supervisor.py') + ' restore ' + self.digest
        startup = self.variant in ('startup-release', 'startup-timeout')
        gate = '/usr/bin/python3 -I -S -B ' + str(self.directory / 'supervisor.py') + ' startup ' + self.digest
        startup_arguments = ['--no-block', '--property=ExecStartPost=' + gate] if startup else []
        command('/usr/bin/systemd-run', '--quiet', '--unit=' + self.unit, '--service-type=exec',
            *startup_arguments,
            '--property=User=root', '--property=Group=root', '--property=UMask=0077',
            '--property=NoNewPrivileges=yes', '--property=Restart=no', '--property=KillMode=control-group',
            '--property=TimeoutStopSec=120', '--property=RuntimeMaxSec=30',
            '--property=RuntimeRandomizedExtraSec=0', '--property=ExecStopPost=' + hook,
            '--property=PrivateNetwork=yes', '--property=PrivateTmp=yes', '--property=ProtectHome=yes',
            '--property=ProtectSystem=strict', '--property=ReadWritePaths=' + str(self.root),
            '--', '/usr/bin/python3', '-I', '-S', '-B', str(self.directory / 'supervisor.py'), 'run', self.digest)

    def entered(self):
        def observed():
            rejected = receipt(self.case, 'rejected')
            if rejected:
                # In particular, a late PID1 activation remains a safe refusal.
                # It is a failed rehearsal, never a reason to relax the timer.
                raise RuntimeError('EXECUTION_SESSION_FIXTURE_ENTRY_REJECTED_' + rejected[1]['reason'])
            return receipt(self.case, 'entered')
        return wait_for(observed)

    def trigger(self, action):
        new_file(self.case / 'trigger.json', encoded(dict(action=action)))

    def checked(self):
        return wait_for(lambda: receipt(self.case, 'checked'))

    def stop(self):
        if show(self.unit, ('LoadState',))['LoadState'] == 'not-found':
            return
        row = show(self.unit, ('FragmentPath', 'ExecStart'))
        need(row['FragmentPath'] == '/run/systemd/transient/' + self.unit and
             'argv[]=/usr/bin/python3 -I -S -B ' + str(self.directory / 'supervisor.py') + ' run ' + self.digest + ' ;' in row['ExecStart'],
             'CLEANUP_UNIT_OWNERSHIP')
        try:
            command('/usr/bin/systemctl', 'stop', self.unit, timeout=10, allow_failure=True)
        except subprocess.TimeoutExpired:
            pass
        if not empty(self.unit):
            command('/usr/bin/systemctl', 'kill', '--kill-whom=all', '--signal=KILL', self.unit, allow_failure=True)
        wait_for(lambda: empty(self.unit), 8)
        command('/usr/bin/systemctl', 'reset-failed', self.unit, allow_failure=True)

    def close(self):
        if self.unit_intended:
            self.stop()


def worker(mode, digest, directory):
    need(mode in ('run', 'restore', 'foreign', 'startup') and re.fullmatch('[0-9a-f]{64}', digest), 'WORKER_ARGUMENTS')
    directory = Path(directory)
    case = directory.parent.parent
    context = json.loads(root_file(case / 'context.json'))
    root = Path(context['root'])
    need(context['purpose'] == PURPOSE.decode().strip() and root_file(root / 'fixture-purpose') == PURPOSE and
         directory == Path(context['directory']) and directory == case / 'execution' / digest and
         unit_name(root, case.name) == context['unit'] and context['digest'] == digest, 'WORKER_SCOPE')
    sys.path.insert(0, str(case / 'source'))
    from ops import light_native_lane_execution as execution
    need(execution.ROOT == case / 'execution' and execution.OWNER_ROOT == case / 'owner' and
         execution.SUPERVISOR == context['unit'], 'STAGED_LAYOUT')
    process = execution.unit_readback._process(os.getpid())
    invocation = process.invocation_id
    need(re.fullmatch('[0-9a-f]{32}', invocation), 'INVOCATION')
    if mode == 'startup':
        need(context['variant'] in ('startup-release', 'startup-timeout'), 'CI_STARTUP_GATE_SCOPE')
        row = execution._session_show(time.monotonic() + 3)
        need(row['ActiveState'] == 'activating' and row['SubState'] == 'start-post' and
             row['ControlPID'] == str(os.getpid()) and int(row['MainPID']) > 1 and
             process.cgroup == '0::/system.slice/' + context['unit'] and
             process.uid == process.gid == 0 and process.no_new_privs == 1, 'ACTUAL_PID1_START_POST')
        value, _, _ = execution.request(digest, for_restore=True)
        new_file(case / ('startup-' + invocation + '.json'), encoded(dict(
            synthetic_property='CI_ONLY_EXEC_START_POST_GATE', pid=process.pid,
            main_pid=int(row['MainPID']), invocation_id=invocation, actual_pid1_control=True,
            observed_monotonic_ns=time.monotonic_ns())))
        wait_for(lambda: (case / 'startup-release').exists(), 12)
        need(root_file(case / 'startup-release') == b'RELEASE_CI_START_POST\n' and
             time.monotonic_ns() < value['clock']['monotonic_end_ns'], 'CI_STARTUP_GATE_RELEASE')
        return
    if mode == 'restore':
        row = execution._session_show(time.monotonic() + 5)
        need(row['ControlPID'] == str(os.getpid()) and process.cgroup == '0::/system.slice/' + context['unit'] and
             process.uid == process.gid == 0 and process.no_new_privs == 1, 'ACTUAL_PID1_CLEANUP')
        restore_valid = True
        try:
            execution.request(digest, for_restore=True)
        except Exception:
            restore_valid = False
        try:
            execution.ExecutionSession.enter(digest)
        except Exception:
            denied = True
        else:
            denied = False
        need(denied, 'CLEANUP_GRANTED_NEW_SESSION')
        new_file(case / ('cleanup-' + invocation + '.json'), encoded(dict(actual_pid1_control=True,
            pid=process.pid, invocation_id=invocation, new_session_denied=denied,
            restore_request_readable=restore_valid, production_restore_exercised=False, workload_executed=False)))
        return
    if mode == 'foreign':
        value, _, _ = execution.request(digest)
        try:
            execution._execution_identity(digest, value, directory)
        except Exception:
            denied = True
        else:
            denied = False
        need(denied, 'FOREIGN_PROCESS_ACCEPTED')
        new_file(case / ('foreign-' + str(os.getpid()) + '.json'), encoded(dict(denied=True,
            actual_pid=process.pid, actual_cgroup=process.cgroup, actual_invocation_id=invocation)))
        return
    if context['variant'] in ('startup-release', 'startup-timeout'):
        row = execution._session_show(time.monotonic() + 3)
        need(row['ActiveState'] == 'activating' and row['SubState'] in ('start', 'start-post') and
             row['MainPID'] == str(os.getpid()) and not (directory / 'session-intent.json').exists()
             and not (directory / 'session-first.json').exists(), 'CI_ACTUAL_ACTIVATING_ENTRY')
        new_file(case / ('attempt-' + invocation + '.json'), encoded(dict(pid=process.pid,
            invocation_id=invocation, observed_monotonic_ns=time.monotonic_ns(),
            actual_activating=True, actual_substate=row['SubState'], no_intent=True, original_end_ns=json.loads(
                root_file(directory / 'request.json'))['clock']['monotonic_end_ns'])))
    try:
        session = execution.ExecutionSession.enter(digest)
    except Exception as error:
        reason = str(error)
        need(isinstance(error, (RuntimeError, FileExistsError)) and
             (reason.startswith('LANE_') or reason == 'NATIVE_UNIT_SHOW_TIMEOUT' or
              isinstance(error, FileExistsError)), 'UNEXPECTED_ENTRY_ERROR')
        new_file(case / ('rejected-' + invocation + '.json'), encoded(dict(rejected=True,
            pid=process.pid, invocation_id=invocation, actual_cgroup=process.cgroup,
            reason='CREATE_ONLY_BARRIER' if isinstance(error, FileExistsError) else reason,
            workload_executed=False, rejected_monotonic_ns=time.monotonic_ns())))
        return
    session.assert_current()
    actual = session.identity
    need(actual['process']['pid'] == os.getpid() and actual['process']['no_new_privs'] == 1 and
         actual['process']['cgroup'] == '0::/system.slice/' + context['unit'] and
         actual['loaded']['MainPID'] == str(os.getpid()), 'ACTUAL_FIRST_IDENTITY')
    timer_start = int(actual['loaded']['ActiveEnterTimestampMonotonic']) * 1000
    timer_ns = int(execution.switch.duration_seconds(actual['loaded']['RuntimeMaxUSec']) * 10**9)
    need(timer_start > 0 and timer_ns >= 30 * 10**9 and
         timer_start + timer_ns <= session.monotonic_end_ns, 'ACTUAL_PID1_TIMER_BOUND')
    if context['variant'] == 'lost-ack':
        os._exit(73)  # Durable actual first binding exists; no entered/ACK receipt.
    terminated = [False]
    signal.signal(signal.SIGTERM, lambda *_: terminated.__setitem__(0, True))
    new_file(case / ('entered-' + invocation + '.json'), encoded(dict(pid=process.pid, invocation_id=invocation,
        first_sha256=sha(root_file(directory / 'session-first.json')),
        identity_sha256=sha(execution.encoded(actual)), monotonic_end_ns=session.monotonic_end_ns,
        actual_pid1_timer_end_ns=timer_start + timer_ns, actual_process=True,
        synthetic_upstream_run_authority=True, workload_executed=False)))
    deadline = time.monotonic() + (42 if context['variant'] == 'timer' else 12)
    while time.monotonic() < deadline:
        trigger = case / 'trigger.json'
        if trigger.exists() or terminated[0]:
            action = 'unit-stopping' if terminated[0] else json.loads(root_file(trigger))['action']
            need(action in ('assert', 'reenter', 'foreign', 'unit-stopping'), 'FIXED_TRIGGER')
            if action == 'reenter':
                try:
                    execution.ExecutionSession.enter(digest)
                except Exception:
                    reentry_denied = True
                else:
                    reentry_denied = False
                need(reentry_denied, 'REENTRY_ACCEPTED')
            elif action == 'foreign':
                result = command('/usr/bin/python3', '-I', '-S', '-B', str(directory / 'supervisor.py'),
                                 'foreign', digest, inherit_invocation=True)
                need(result.returncode == 0, 'FOREIGN_CHECK')
                session.assert_current()
                # The genuine MainPID remains valid after the child denial.
                # Then change a real /proc field without replacing the collector.
                os.chdir(case)
                need(execution.unit_readback._process(os.getpid()).cwd == str(case), 'ACTUAL_CWD_DRIFT')
            try:
                session.assert_current()
            except RuntimeError as error:
                need(str(error) == 'LANE_EXEC_SESSION_CLOSED', 'CLOSED_REASON')
                accepted = False
            else:
                accepted = True
            if not accepted:
                try:
                    session.assert_current()
                except RuntimeError as error:
                    need(str(error) == 'LANE_EXEC_SESSION_CLOSED', 'STICKY_CLOSE')
                else:
                    raise RuntimeError('EXECUTION_SESSION_FIXTURE_REOPENED')
            new_file(case / ('checked-' + invocation + '.json'), encoded(dict(action=action, accepted=accepted,
                sticky_closed=not accepted, monotonic_end_ns=session.monotonic_end_ns,
                checked_monotonic_ns=time.monotonic_ns(), workload_executed=False,
                valid_after_child=action == 'foreign', actual_cwd_drift=action == 'foreign')))
            return
        time.sleep(.025)
    raise RuntimeError('EXECUTION_SESSION_FIXTURE_TRIGGER_TIMEOUT')


def mark(case, **evidence):
    print(json.dumps(dict(case=case, result='PASS', real_systemd=True, real_proc=True,
        production_accepted=False, upstream_run_authenticated=False, workload_executed=False, **evidence)), flush=True)


def activating_without_intent(layout):
    startup = wait_for(lambda: receipt(layout.case, 'startup'))[1]
    attempt = wait_for(lambda: receipt(layout.case, 'attempt'))[1]
    need(startup['actual_pid1_control'] and startup['main_pid'] == attempt['pid'] and
         startup['invocation_id'] == attempt['invocation_id'] and attempt['actual_activating'] and
         attempt['no_intent'], 'ACTUAL_STARTUP_ATTEMPT')
    # The fixed post-start process holds actual PID1 state; these observations
    # never substitute loaded fields or implement a fixture readiness retry.
    deadline = time.monotonic() + .15
    while time.monotonic() < deadline:
        row = show(layout.unit, ('ActiveState', 'SubState', 'MainPID', 'ControlPID', 'InvocationID'))
        need(row == dict(ActiveState='activating', SubState='start-post', MainPID=str(attempt['pid']),
             ControlPID=str(startup['pid']), InvocationID=attempt['invocation_id']) and
             not (layout.directory / 'session-intent.json').exists() and
             not (layout.directory / 'session-first.json').exists() and
             not receipt(layout.case, 'rejected'), 'ACTIVATING_WROTE_INTENT_OR_REFUSED_EARLY')
        time.sleep(.025)
    return attempt


def run(repo, source):
    need(sys.platform == 'linux' and os.geteuid() == 0 and
         Path('/proc/1/comm').read_text().strip() == 'systemd', 'ACTUAL_PID1_REQUIRED')
    need(os.environ.get('GITHUB_ACTIONS') == 'true' and
         os.environ.get('GITHUB_REPOSITORY') == 'olegmed1-art/bridge-video-free', 'HOSTED_CI_ONLY')
    need(repo.is_absolute() and repo.is_dir() and re.fullmatch('[0-9a-f]{40}', source), 'SOURCE')
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir='/run'))
    root.chmod(0o700)
    root_identity = (root.stat().st_dev, root.stat().st_ino)
    layouts = []
    started = time.monotonic()
    try:
        new_file(root / 'fixture-purpose', PURPOSE)
        def prepared(label, **kwargs):
            layout = Layout(root, label)
            layouts.append(layout)
            value = layout.prepare(repo, source, **kwargs)
            layout.launch()
            return layout, value
        first, first_value = prepared('first', variant='startup-release')
        attempt = activating_without_intent(first)
        new_file(first.case / 'startup-release', b'RELEASE_CI_START_POST\n')
        entered = first.entered()[1]
        need(attempt['original_end_ns'] == entered['monotonic_end_ns'] ==
             first_value['clock']['monotonic_end_ns'], 'STARTUP_REBASED_DEADLINE')
        first.trigger('reenter')
        need(first.checked()[1]['accepted'] and sha(root_file(first.directory / 'session-first.json')) == entered['first_sha256'], 'SAME_PID_REENTRY')
        first.stop()
        startup_timeout, timeout_value = prepared('first-timeout', variant='startup-timeout')
        attempt = activating_without_intent(startup_timeout)
        refused = wait_for(lambda: receipt(startup_timeout.case, 'rejected'), 6)[1]
        elapsed = refused['rejected_monotonic_ns'] - attempt['observed_monotonic_ns']
        need(refused['reason'] in ('LANE_EXEC_SESSION_READINESS', 'NATIVE_UNIT_SHOW_TIMEOUT') and
             refused['rejected_monotonic_ns'] < timeout_value['clock']['monotonic_end_ns'] and
             2_500_000_000 <= elapsed <= 5 * 10**9 and
             not (startup_timeout.directory / 'session-intent.json').exists() and
             not (startup_timeout.directory / 'session-first.json').exists(), 'STARTUP_WAIT_UNBOUNDED_OR_WROTE_INTENT')
        startup_timeout.stop()
        need(not (startup_timeout.directory / 'session-intent.json').exists() and
             not (startup_timeout.directory / 'session-first.json').exists(), 'STARTUP_CLEANUP_CREATED_SESSION')
        mark('first', actual_pid1_timer_bound=True, same_pid_reentry_denied=True,
             ci_only_exec_start_post_gate=True, activating_no_intent=True,
             production_readiness_used=True, never_release_bounded_refusal=True,
             original_deadline_unchanged=True)

        restarted, _ = prepared('restart')
        old = restarted.entered()
        original_first = root_file(restarted.directory / 'session-first.json')
        command('/usr/bin/systemctl', 'restart', restarted.unit)
        reject = wait_for(lambda: receipt(restarted.case, 'rejected'))[1]
        need(reject['invocation_id'] != old[1]['invocation_id'] and
             root_file(restarted.directory / 'session-first.json') == original_first, 'RESTART_REBOUND')
        restarted.stop()
        mark('restart', new_invocation_denied=True, first_binding_unchanged=True)

        foreign, _ = prepared('foreign')
        actual = foreign.entered()[1]
        foreign.trigger('foreign')
        checked = foreign.checked()[1]
        need(checked['valid_after_child'] and checked['actual_cwd_drift'] and checked['sticky_closed'],
             'FOREIGN_OR_PROC_DRIFT_NOT_EXERCISED')
        denied = wait_for(lambda: receipt(foreign.case, 'foreign'))[1]
        need(denied['denied'] and denied['actual_pid'] != actual['pid'] and
             denied['actual_cgroup'] == '0::/system.slice/' + foreign.unit, 'FOREIGN_NOT_EXERCISED')
        foreign.stop()
        cleanup = wait_for(lambda: receipt(foreign.case, 'cleanup'))[1]
        need(cleanup['actual_pid1_control'] and cleanup['restore_request_readable'] and
             cleanup['new_session_denied'], 'CLOSED_SESSION_CLEANUP')
        mark('foreign', actual_non_main_process_denied=True, actual_main_pid_cwd_drift_denied=True,
             closed_session_independent_cleanup=True)

        wrong, _ = prepared('python', variant='wrong-python')
        reject = wait_for(lambda: receipt(wrong.case, 'rejected'))[1]
        need(reject['reason'] == 'LANE_EXEC_SESSION_PROCESS' and
             not (wrong.directory / 'session-intent.json').exists() and
             not (wrong.directory / 'session-first.json').exists(), 'PYTHON_PIN_NOT_EXERCISED')
        wrong.stop()
        mark('python', actual_python_pin_mismatch_denied=True)

        for name in ('script', 'request', 'context', 'pointer'):
            drift, _ = prepared('inputs-' + name)
            drift.entered()
            if name == 'script': path = drift.directory / 'supervisor.py'
            elif name == 'request': path = drift.directory / 'request.json'
            elif name == 'context': path = drift.directory / 'owner-context.json'
            else:
                body = json.loads(root_file(drift.directory / 'owner-context.json'))
                path = drift.owner / body['owner_plan_sha256'] / 'execution.json'
            original = root_file(path)
            replace_owned(path, original + (b'\n# fixture byte drift\n' if name == 'script' else b'\n'))
            drift.trigger('assert')
            need(drift.checked()[1]['sticky_closed'], 'INPUT_DRIFT_ACCEPTED')
            drift.stop()
        mark('inputs', source_request_context_pointer_drift_denied=True)

        stopping, _ = prepared('unit')
        stopping.entered()
        command('/usr/bin/systemctl', 'stop', '--no-block', stopping.unit)
        stopped = stopping.checked()[1]
        need(stopped['action'] == 'unit-stopping' and stopped['sticky_closed'], 'UNIT_DRIFT_NOT_EXERCISED')
        stopping.stop()
        mark('unit', actual_pid1_stop_state_denied=True)

        timer, value = prepared('expiry', variant='timer', window=35)
        timer.entered()
        closed = wait_for(lambda: receipt(timer.case, 'checked'), 38)[1]
        need(closed['sticky_closed'] and closed['action'] == 'unit-stopping', 'ACTUAL_TIMER_NOT_EXERCISED')
        wait_for(lambda: empty(timer.unit), 8)
        original_first = root_file(timer.directory / 'session-first.json')
        while time.monotonic_ns() <= value['clock']['monotonic_end_ns']:
            time.sleep(.025)
        timer.launch()
        need(wait_for(lambda: receipt(timer.case, 'rejected'))[1]['rejected'] and
             root_file(timer.directory / 'session-first.json') == original_first, 'EXPIRY_RENEWED')
        timer.stop()
        mark('expiry', actual_pid1_runtime_expired=True, original_deadline_not_renewed=True)

        partial, _ = prepared('uncertain-partial', variant='partial')
        wait_for(lambda: receipt(partial.case, 'rejected'))
        need(partial.barrier_unchanged() and
             not (partial.directory / 'session-first.json').exists(), 'PARTIAL_BARRIER_REPLACED')
        cleanup = wait_for(lambda: receipt(partial.case, 'cleanup'))[1]
        need(cleanup['actual_pid1_control'] and cleanup['restore_request_readable'] and
             cleanup['new_session_denied'], 'PARTIAL_CLEANUP')
        partial.stop()
        need(partial.barrier_unchanged(), 'PARTIAL_CLEANUP_REPLACED_BARRIER')
        orphan, _ = prepared('uncertain-orphan', variant='orphan-first')
        wait_for(lambda: receipt(orphan.case, 'rejected'))
        need(orphan.barrier_unchanged() and
             not (orphan.directory / 'session-intent.json').exists(), 'ORPHAN_FIRST_REPLACED')
        orphan.stop()
        need(orphan.barrier_unchanged() and
             not (orphan.directory / 'session-intent.json').exists(), 'ORPHAN_CLEANUP_CREATED_SESSION')
        lost, _ = prepared('uncertain-lost', variant='lost-ack')
        wait_for(lambda: (lost.directory / 'session-first.json').exists())
        cleanup = wait_for(lambda: receipt(lost.case, 'cleanup'))[1]
        first_bytes = root_file(lost.directory / 'session-first.json')
        need(not list(lost.case.glob('entered-*.json')) and cleanup['actual_pid1_control'] and
             cleanup['restore_request_readable'] and cleanup['new_session_denied'], 'LOST_ACK_NOT_EXERCISED')
        wait_for(lambda: empty(lost.unit) and show(lost.unit, ('ActiveState',))['ActiveState'] in
                 ('inactive', 'failed'), 8)
        lost.launch()
        wait_for(lambda: receipt(lost.case, 'rejected'))
        need(root_file(lost.directory / 'session-first.json') == first_bytes, 'LOST_ACK_REBOUND')
        lost.stop()
        mark('uncertain', partial_and_lost_ack_barriers=True, orphan_first_barrier=True,
             actual_independent_cleanup=True)
        print(json.dumps(dict(audit='EXECUTION_SESSION_SYSTEMD_REHEARSAL', passed=len(CASES),
            isolated_units=len(layouts), ci_only_exec_start_post_gate=True,
            real_systemd=True, real_proc=True, production_accepted=False, host_delegation=False,
            database_connected=False, provider_connected=False, workload_executed=False,
            production_restore_exercised=False, elapsed_seconds=round(time.monotonic() - started, 1))), flush=True)
    finally:
        failures = []
        for layout in layouts:
            try: layout.close()
            except Exception: failures.append(layout.label)
        need(not failures, 'OWNED_CLEANUP_FAILED')
        row = root.stat(follow_symlinks=False)
        need(root.parent == Path('/run') and root.resolve() == root and not root.is_symlink() and
             (row.st_dev, row.st_ino) == root_identity and root_file(root / 'fixture-purpose') == PURPOSE,
             'ROOT_CLEANUP_OWNERSHIP')
        shutil.rmtree(root)


if __name__ == '__main__':
    need(len(sys.argv) == 4 and sys.argv[1] == 'ci', 'ARGUMENTS')
    run(Path(sys.argv[2]).resolve(), sys.argv[3])
