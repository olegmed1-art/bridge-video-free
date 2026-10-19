"""Local stdlib contract tests; Linux process/filesystem evidence is separate."""
import base64
import io
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import os
import stat
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import light_native_service_authority as a


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def equal_stat_rows(field='st_mtime_ns'):
    extras = dict(st_atime_ns=100*10**9, st_mtime_ns=101*10**9, st_ctime_ns=102*10**9, st_rdev=0)
    row = os.stat_result((stat.S_IFREG | 0o600, 2, 3, 1, 0, 0, 8, 100, 101, 102), extras)
    changed = os.stat_result(tuple(row), {**extras, field: extras[field]+1})
    return row, changed


def make_fixture(count=2):
    now = int(time.time())
    entries = []
    for index in range(count):
        spec = dict(assignment_schema='SLAVIK_DISPATCH_ASSIGNMENT_V1', repository=a.REPOSITORY,
                    target_pr=2000+index, expected_head_sha=str(index+1)*40,
                    execution_mode='READ_ONLY', exact_head_binding=True,
                    cost_cap_microusd=0, max_repair_attempts=0, **dict.fromkeys(a.FALSE_FLAGS, False))
        raw = a.encoded(dict(version=1, source=a.RETAINED_SOURCE, repository=a.REPOSITORY,
            target_pr=spec['target_pr'], expected_head_sha=spec['expected_head_sha'],
            branch=f'fix/service-audit-{index}', work_key=f'service-audit-{index}',
            objective='Read the exact source only.', priority=0, task_spec_json=spec))
        entries.append(dict(plan_base64=base64.b64encode(raw).decode(), accepted_plan_sha256=a.sha(raw)))
    controller, runtime = b'controller-fixture', b'runtime-fixture'
    policy = dict(version=1, action='issue', source='a'*40,
        accepted_controller_sha256=a.sha(controller), accepted_runtime_sha256=a.sha(runtime),
        not_before=stamp(now-60), expires_at=stamp(now+7200),
        predecessor=dict(plan_sha256='b'*64, terminal_sha256='c'*64, sequence=2), plans=entries,
        authority=dict(owner=a.OWNER, coverage=a.COVERAGE, delegation='FINITE_ISSUER_AGREEMENTS',
                       evidence='Explicit independently accepted finite READ_ONLY catalogue.'))
    raw = a.encoded(policy)
    manifest = dict(version=1, kind='LIGHT_NATIVE_READ_ONLY_SERVICE', source=policy['source'],
        controller_sha256=a.sha(controller), runtime_sha256=a.sha(runtime), policy_sha256=a.sha(raw),
        unit_sha256=a.sha(b'unit'), python_sha256=a.sha(b'python'),
        authority_sha256=a.sha(b'authority'), supervisor_sha256=a.sha(b'supervisor'),
        launcher_sha256=a.sha(b'launcher'),
        argv=['/usr/bin/python3', '-I', '-S', '-B', a.ROOT.as_posix()+'/launcher.py'],
        not_before=policy['not_before'], expires_at=policy['expires_at'])
    manifest_raw = a.encoded(manifest)
    install = dict(version=1, kind='ROOT_ACCEPTED_SERVICE_INSTALLATION', owner=a.OWNER,
        manifest_sha256=a.sha(manifest_raw), **{k: manifest[k] for k in
        ('source', 'controller_sha256', 'runtime_sha256', 'policy_sha256', 'unit_sha256')},
        accepted_at=stamp(now-10), evidence_sha256='d'*64,
        accepted_boot_id='a'*8+'-'+'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
        accepted_wall_ns=(now-10)*10**9, accepted_monotonic_ns=time.monotonic_ns()-10*10**9)
    process = a.ProcessIdentity(2345, 10000, 'a'*8+'-'+'b'*4+'-'+'c'*4+'-'+'d'*4+'-'+'e'*12,
        manifest['python_sha256'], tuple(manifest['argv']), '0::/system.slice/'+a.UNIT, 'f'*32)
    return SimpleNamespace(now=now, policy=policy, raw=raw, manifest=manifest,
        manifest_raw=manifest_raw, install=install, process=process, controller=controller, runtime=runtime)


@contextmanager
def loaded_fixture(fixture=None):
    f = make_fixture() if fixture is None else fixture
    temp_parent = Path.home()/'AppData'/'Local'/'Temp' if sys.platform == 'win32' else None
    with tempfile.TemporaryDirectory(dir=temp_parent) as directory:
        root = Path(directory)
        records = {root/'manifest.json': f.manifest_raw, root/'installation.json': a.encoded(f.install),
                   root/'policy.json': f.raw, root/'controller.json': f.controller,
                   root/'runtime.json': f.runtime, Path(a.__file__): b'authority',
                   root/'launcher.py': b'launcher',
                   Path(a.__file__).with_name('light_native_service_supervisor.py'): b'supervisor'}
        def read(path, expected=None, **kwargs):
            if Path(path) not in records:
                raise FileNotFoundError()
            raw = records[Path(path)]
            a.require(expected is None or a.sha(raw) == expected, 'TEST_BINDING')
            return raw
        own = dict(cgroup=f.process.cgroup, exe_sha256=f.process.exe_sha256)
        def retain(context_root, raw):
            path = Path(context_root)/'contexts'/(a.sha(raw)+'.json')
            if path in records:
                a.require(records[path] == raw)
                return
            records[path] = raw
        with patch.object(a, 'protected_read', side_effect=read), \
             patch.object(a, 'root_directory'), patch.object(a, 'host_identity', return_value=f.process), \
             patch.object(a, 'proc_identity', return_value=own), patch.object(a, '_descendant', return_value=True), \
             patch.object(a, '_packages'), patch.object(a, '_current_main'), \
             patch.object(a, '_retain_context', side_effect=retain), \
             patch.object(a.os, 'uname', return_value=SimpleNamespace(nodename=a.HOST), create=True):
            host = a.HostAuthority(a.sha(f.manifest_raw), root=root)
            yield SimpleNamespace(host=host, records=records, root=root, fixture=f)


class AuthorityContracts(unittest.TestCase):
    def test_equal_stat_tuples_with_different_ns_have_distinct_signatures(self):
        for field in ('st_atime_ns', 'st_mtime_ns', 'st_ctime_ns'):
            row, changed = equal_stat_rows(field)
            with self.subTest(field=field):
                self.assertEqual(row, changed)
                self.assertNotEqual(getattr(row, field), getattr(changed, field))
                self.assertNotEqual(a.metadata_signature(row), a.metadata_signature(changed))

    def test_protected_read_rejects_equal_tuple_ns_drift_on_fd_and_path(self):
        for field in ('st_atime_ns', 'st_mtime_ns', 'st_ctime_ns'):
            row, changed = equal_stat_rows(field)
            for endpoint in ('fd', 'path'):
                with self.subTest(field=field, endpoint=endpoint), \
                     patch.object(a, 'root_directory'), patch.object(a.os, 'open', return_value=17), \
                     patch.object(a.os, 'close'), patch.object(a.os, 'O_NOATIME', 0x40000, create=True), \
                     patch.object(a.os, 'O_NOFOLLOW', 0x20000, create=True), \
                     patch.object(a.os, 'O_NONBLOCK', 0x800, create=True), \
                     patch.object(a.os, 'fstat', side_effect=[row, changed if endpoint == 'fd' else row]), \
                     patch.object(a.os, 'fdopen', return_value=io.BytesIO(b'accepted')), \
                     patch.object(Path, 'stat', return_value=changed if endpoint == 'path' else row):
                    with self.assertRaisesRegex(a.Refusal, 'NATIVE_SERVICE_FILE_CHANGED'):
                        a.protected_read(Path('accepted-record'))

    def test_exact_finite_manifest_and_readonly_catalogue(self):
        f = make_fixture()
        manifest = a.Manifest.load(f.manifest_raw)
        policy = a.validate_policy(f.raw, manifest)
        self.assertEqual(policy.predecessor, f.policy['predecessor'])
        self.assertEqual(len(policy.plans), 2)
        self.assertEqual(policy.end-policy.start, 7260)
        self.assertEqual(policy.plans[0].head_sha, '1'*40)

    def test_eight_exact_plans_and_twenty_four_hour_boundary(self):
        f = make_fixture(8)
        end = stamp(a.timestamp(f.policy['not_before'])+86400)
        raw = a.encoded({**f.policy, 'expires_at': end})
        manifest = a.Manifest.load(a.encoded({**f.manifest, 'expires_at': end, 'policy_sha256': a.sha(raw)}))
        self.assertEqual(len(a.validate_policy(raw, manifest).plans), 8)
        raw = a.encoded({**f.policy, 'expires_at': stamp(a.timestamp(end)+1)})
        manifest = a.Manifest.load(a.encoded({**f.manifest, 'policy_sha256': a.sha(raw)}))
        with self.assertRaises(a.Refusal): a.validate_policy(raw, manifest)

    def test_noncanonical_duplicate_unknown_or_credential_fields_refuse(self):
        f = make_fixture()
        for raw in (f.manifest_raw+b' ', b'{"version":1,"version":1}',
                    a.encoded({**f.manifest, 'credential': 'private-secret'}),
                    a.encoded({**f.manifest, 'version': True}),
                    a.encoded({**f.manifest, 'expires_at': stamp(f.now+90000)}),
                    a.encoded({**f.manifest, 'argv': ['/usr/bin/python3', 'service.py']})):
            with self.subTest(raw=raw[:40]), self.assertRaises(a.Refusal):
                a.Manifest.load(raw)

    def test_interpreter_flags_must_precede_the_exact_pinned_launcher(self):
        f = make_fixture()
        for argv in (['/usr/bin/python3', '/tmp/other.py', '-I', '-S', '-B'],
                     ['/usr/bin/python3', '-I', '-S', '-B', '/tmp/other.py'],
                     f.manifest['argv']+['extra-argument']):
            with self.subTest(argv=argv), self.assertRaises(a.Refusal):
                a.Manifest.load(a.encoded({**f.manifest, 'argv': argv}))

    def test_noatime_unavailable_never_falls_back_to_ordinary_read(self):
        with patch.object(a, 'root_directory'), \
             patch.object(a.os, 'O_NOATIME', None, create=True), patch.object(a.os, 'open') as opened:
            with self.assertRaisesRegex(a.Refusal, 'NATIVE_SERVICE_NOATIME_REQUIRED'):
                a.protected_read(Path('accepted-record'))
            opened.assert_not_called()

    def test_policy_rejects_more_work_duration_renewal_and_mutation(self):
        f = make_fixture()
        for change in ('count', 'empty', 'duplicate', 'paid', 'repair', 'write', 'verify',
                       'head', 'source', 'runtime', 'authority', 'long', 'predecessor', 'renewal'):
            value = deepcopy(f.policy)
            if change == 'count': value['plans'] *= 5
            if change == 'empty': value['plans'] = []
            if change == 'duplicate': value['plans'][1] = value['plans'][0]
            if change in ('paid', 'repair', 'write', 'verify', 'head'):
                plan = a.parse(base64.b64decode(value['plans'][0]['plan_base64']))
                spec = plan['task_spec_json']
                if change == 'paid': spec['cost_cap_microusd'] = 1
                if change == 'repair': spec['max_repair_attempts'] = 1
                if change == 'write': spec['repository_mutation'] = True
                if change == 'verify': spec['execution_mode'] = 'VERIFY'
                if change == 'head': spec['expected_head_sha'] = '0'*40
                raw_plan = a.encoded(plan)
                value['plans'][0] = dict(plan_base64=base64.b64encode(raw_plan).decode(),
                                         accepted_plan_sha256=a.sha(raw_plan))
            if change == 'source': value['source'] = '0'*40
            if change == 'runtime': value['accepted_runtime_sha256'] = '0'*64
            if change == 'authority': value['authority']['delegation'] = 'CONTINUOUS'
            if change == 'long': value['expires_at'] = stamp(f.now+90000)
            if change == 'predecessor': value['predecessor']['sequence'] = True
            if change == 'renewal': value['renew'] = True
            raw = a.encoded(value)
            manifest = a.Manifest.load(a.encoded({**f.manifest, 'policy_sha256': a.sha(raw)}))
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                a.validate_policy(raw, manifest)

    def test_version_two_retirement_pins_are_exact_and_preserved(self):
        from ops import light_native_retirement as retirement
        f = make_fixture()
        value = {**f.policy, 'version': 2,
            'retirements': [dict(policy_sha256='d'*64, index=0, record_sha256='e'*64)],
            'retirement_evidence': {'d'*64: dict.fromkeys(retirement.JOURNAL_KEYS, 'f'*64)}}
        raw = a.encoded(value)
        manifest = a.Manifest.load(a.encoded({**f.manifest, 'policy_sha256': a.sha(raw)}))
        self.assertEqual(a.validate_policy(raw, manifest).raw, raw)
        del value['retirement_evidence']['d'*64]['issuer-intent']
        raw = a.encoded(value)
        manifest = a.Manifest.load(a.encoded({**f.manifest, 'policy_sha256': a.sha(raw)}))
        with self.assertRaises(RuntimeError): a.validate_policy(raw, manifest)

    def test_installed_provenance_and_context_bind_real_invocation(self):
        with loaded_fixture() as f:
            context = f.host.child_context()
            self.assertEqual(a.ChildContext.load(context.wire()), context)
            self.assertLessEqual(context.expires_at-context.not_before, 1800)
            self.assertEqual(context.process, f.fixture.process)
            self.assertNotIn(b'run_id', context.wire())
            self.assertNotIn(b'token', context.wire())
            child = a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())
            self.assertEqual(child.process, f.host.process)
            with self.assertRaises(a.Refusal): child.child_context()
            for field, changed in (('source', '0'*40), ('installation_sha256', '0'*64),
                                   ('unit', 'other.service'), ('expires_at', context.not_before+1801)):
                wire = {**asdict(context), field: changed}
                with self.subTest(field=field), self.assertRaises(a.Refusal):
                    a.HostAuthority(f.host.accepted, root=f.root, child_context=a.encoded(wire))

    def test_hash_alone_cannot_substitute_installation_acceptance(self):
        with loaded_fixture() as f:
            install = {**f.fixture.install, 'manifest_sha256': '0'*64}
            f.records[f.root/'installation.json'] = a.encoded(install)
            with self.assertRaises(a.Refusal): a.HostAuthority(f.host.accepted, root=f.root)

    def test_restart_wall_rollback_cannot_rebase_the_accepted_monotonic_deadline(self):
        with loaded_fixture() as f:
            deadline = f.host.monotonic_deadline_ns
            anchor = f.fixture.install['accepted_monotonic_ns']
            with patch.object(a.time, 'time', return_value=f.fixture.now+100), \
                 patch.object(a.time, 'monotonic_ns', return_value=anchor+110*10**9):
                first = a.HostAuthority(f.host.accepted, root=f.root)
            with patch.object(a.time, 'time', return_value=f.fixture.now+50), \
                 patch.object(a.time, 'monotonic_ns', return_value=anchor+111*10**9):
                restarted = a.HostAuthority(f.host.accepted, root=f.root)
            self.assertEqual(first.monotonic_deadline_ns, deadline)
            self.assertEqual(restarted.monotonic_deadline_ns, deadline)
            # Wall time is still inside the manifest, but real accepted time ended.
            with patch.object(a.time, 'time', return_value=f.fixture.now+50), \
                 patch.object(a.time, 'monotonic_ns', return_value=deadline+1):
                with self.assertRaises(a.Refusal): a.HostAuthority(f.host.accepted, root=f.root)

    def test_restart_on_another_boot_refuses_the_old_installation(self):
        with loaded_fixture() as f:
            a.host_identity.return_value = replace(f.fixture.process, boot_id='0'*8+'-'+'0'*4+'-'+'0'*4+'-'+'0'*4+'-'+'0'*12)
            with self.assertRaisesRegex(a.Refusal, 'NATIVE_SERVICE_BOOT_CHANGED'):
                a.HostAuthority(f.host.accepted, root=f.root)

    def test_rehashed_context_cannot_extend_or_convert_retained_observe_authority(self):
        with loaded_fixture() as f:
            context = f.host.child_context(seconds=900)
            changed = asdict(context)
            changed['expires_at'] += 1
            changed['monotonic_end_ns'] += 10**9
            converted = {**asdict(context), 'purpose': 'ISSUE', 'index': 0,
                'plan_sha256': f.host.policy.plans[0].plan_sha256,
                'predecessor_sha256': a.sha(a.encoded(f.host.policy.predecessor)),
                'intent_binding_sha256': 'e'*64}
            for value in (changed, converted):
                with self.subTest(purpose=value['purpose']), self.assertRaises(a.Refusal):
                    a.HostAuthority(f.host.accepted, root=f.root, child_context=a.encoded(value))
            observed = a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())
            with self.assertRaises(a.Refusal):
                observed.assert_issue(f.host.policy.digest, 0, f.host.policy.plans[0].plan_sha256)

    def test_issue_context_requires_exact_retained_intent_and_scope(self):
        with loaded_fixture() as f:
            binding = dict(version=1, kind='LIGHT_NATIVE_SERVICE_ISSUE_INTENT',
                manifest_sha256=f.host.accepted, policy_sha256=f.host.policy.digest, index=0,
                plan_sha256=f.host.policy.plans[0].plan_sha256,
                predecessor=f.host.policy.predecessor, observation_sha256='e'*64)
            context = f.host.child_context(purpose='ISSUE', binding=binding)
            with self.assertRaises(a.Refusal):
                a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())
            path = f.root/'journal'/'0000'/'intent.json'
            intent = {**binding, 'context': asdict(context)}
            f.records[path] = a.encoded(intent)
            scoped = a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())
            scoped.assert_issue(f.host.policy.digest, 0, f.host.policy.plans[0].plan_sha256)
            with self.assertRaises(a.Refusal):
                scoped.assert_issue(f.host.policy.digest, 1, f.host.policy.plans[1].plan_sha256)
            changed_index = {**asdict(context), 'index': 1, 'plan_sha256': f.host.policy.plans[1].plan_sha256}
            with self.assertRaises(a.Refusal):
                a.HostAuthority(f.host.accepted, root=f.root, child_context=a.encoded(changed_index))
            f.records[path] = a.encoded({**intent, 'observation_sha256': 'f'*64})
            with self.assertRaises(a.Refusal): scoped.assert_current()

    def test_replaced_context_record_and_wrong_invocation_refuse(self):
        with loaded_fixture() as f:
            context = f.host.child_context()
            scoped = a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())
            path = f.root/'contexts'/(a.sha(context.wire())+'.json')
            f.records[path] = a.encoded({**asdict(context), 'expires_at': context.expires_at-1})
            with self.assertRaises(a.Refusal): scoped.assert_current()
        with loaded_fixture() as f:
            context = f.host.child_context()
            a.host_identity.return_value = replace(f.fixture.process, invocation_id='0'*32)
            with self.assertRaises(a.Refusal):
                a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())

    def test_stop_file_source_process_and_runtime_drift_are_sticky(self):
        for fault in ('stop', 'source', 'process', 'runtime', 'controller', 'manifest', 'installation', 'code'):
            with self.subTest(fault=fault), loaded_fixture() as f:
                if fault == 'stop': (f.root/'STOP').write_text('stop')
                elif fault == 'source': a._current_main.side_effect = RuntimeError('private-token')
                elif fault == 'process': a.host_identity.return_value = None
                else:
                    name = {'runtime': 'runtime.json', 'controller': 'controller.json', 'manifest': 'manifest.json',
                            'installation': 'installation.json'}.get(fault)
                    path = Path(a.__file__) if fault == 'code' else f.root/name
                    f.records[path] = b'changed'
                with self.assertRaisesRegex(a.Refusal, '^NATIVE_SERVICE_AUTHORITY_CLOSED$'):
                    f.host.assert_current()
                self.assertTrue(f.host.failed)
                a._current_main.side_effect = None
                with self.assertRaises(a.Refusal): f.host.assert_current()

    def test_wall_rollback_and_monotonic_expiry_refuse(self):
        with loaded_fixture() as f, patch.object(a.time, 'time', return_value=f.host.last_wall-1):
            with self.assertRaises(a.Refusal): f.host.assert_current()
        with loaded_fixture() as f, patch.object(a.time, 'monotonic_ns', return_value=f.host.monotonic_deadline_ns+1):
            with self.assertRaises(a.Refusal): f.host.assert_current()

    def test_main_check_cannot_run_past_authority_deadline(self):
        with loaded_fixture() as f:
            calls = [f.host.last_wall+0.01, f.fixture.now+8000]
            with patch.object(a.time, 'time', side_effect=calls), self.assertRaises(a.Refusal):
                f.host.assert_current()

    def test_process_drift_during_main_observation_is_rejected(self):
        with loaded_fixture() as f:
            def changed(source):
                a.host_identity.return_value = None
            a._current_main.side_effect = changed
            with self.assertRaises(a.Refusal): f.host.assert_current()

    def test_stop_and_immutable_drift_during_main_observation_refuse(self):
        for fault in ('STOP', 'policy.json', 'manifest.json', 'installation.json', 'runtime.json', 'controller.json', 'launcher.py'):
            with self.subTest(fault=fault), loaded_fixture() as f:
                def changed(source):
                    if fault == 'STOP': (f.root/'STOP').write_text('stop')
                    else: f.records[f.root/fault] = b'changed'
                a._current_main.side_effect = changed
                with self.assertRaises(a.Refusal): f.host.assert_current()

    def test_actual_host_port_requires_exact_systemd_proc_and_unit_binding(self):
        f = make_fixture()
        manifest = a.Manifest.load(f.manifest_raw)
        row = asdict(f.process)
        del row['invocation_id']
        row['argv'] = tuple(row['argv'])
        values = dict(MainPID=str(f.process.pid), ActiveState='active', SubState='running',
                      InvocationID=f.process.invocation_id,
                      FragmentPath=str(Path('/etc/systemd/system')/a.UNIT))
        def response(value):
            return SimpleNamespace(returncode=0, stdout=''.join(k+'='+v+'\n' for k,v in value.items()).encode())
        with patch.object(a, 'proc_identity', return_value=row), \
             patch.object(a, 'protected_read'), patch.object(a.subprocess, 'run', return_value=response(values)) as run:
            self.assertEqual(a.host_identity(f.process.pid, manifest), f.process)
            for key, bad in (('MainPID', '999'), ('InvocationID', '0'*32), ('SubState', 'exited'),
                             ('FragmentPath', '/tmp/other.service')):
                changed = {**values, key: bad}
                # A different valid invocation is captured on restart; a guard
                # comparison, rather than this snapshot reader, rejects drift.
                if key == 'InvocationID':
                    run.return_value = response(changed)
                    self.assertNotEqual(a.host_identity(f.process.pid, manifest), f.process)
                    continue
                run.return_value = response(changed)
                with self.subTest(key=key), self.assertRaises(a.Refusal):
                    a.host_identity(f.process.pid, manifest)

    def test_child_expiry_and_non_descendant_fail_closed(self):
        with loaded_fixture() as f:
            context = f.host.child_context(seconds=1)
            with patch.object(a.time, 'monotonic_ns', return_value=context.monotonic_end_ns+1):
                with self.assertRaises(a.Refusal):
                    a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())
            a._descendant.return_value = False
            with self.assertRaises(a.Refusal):
                a.HostAuthority(f.host.accepted, root=f.root, child_context=context.wire())


@unittest.skipUnless(sys.platform == 'linux', 'NOTRUN: actual Linux /proc identity requires Linux')
class LinuxProcessEvidence(unittest.TestCase):
    def test_actual_self_boot_start_exe_argv_and_cgroup(self):
        row = a.proc_identity(os.getpid())
        self.assertEqual(row['pid'], os.getpid())
        self.assertGreater(row['start_ticks'], 0)
        self.assertTrue(a.digest(row['exe_sha256']))
        self.assertEqual(row, a.proc_identity(os.getpid()))
        self.assertTrue(a._descendant(os.getpid()))


@unittest.skipUnless(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'NOTRUN: protected-file metadata and root ancestors require Linux root')
class LinuxFilesystemEvidence(unittest.TestCase):
    def test_real_noatime_read_is_stable_when_atime_is_older_than_mtime(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'root_directory'):
            path = Path(directory)/'record'
            raw = b'accepted immutable bytes'
            path.write_bytes(raw)
            path.chmod(0o600)
            now = time.time_ns()
            os.utime(path, ns=(now-2*86400*10**9, now-3600*10**9))
            before = path.stat(follow_symlinks=False)
            self.assertLess(before.st_atime_ns, before.st_mtime_ns)
            original_open = os.open
            with patch.object(a.os, 'open', wraps=original_open) as opened:
                self.assertEqual(a.protected_read(path, a.sha(raw)), raw)
                self.assertEqual(a.protected_read(path, a.sha(raw)), raw)
                self.assertTrue(all(call.args[1] & os.O_NOATIME for call in opened.call_args_list))
            self.assertEqual(a.metadata_signature(path.stat(follow_symlinks=False)), a.metadata_signature(before))

    def test_real_metadata_drift_during_noatime_read_still_refuses(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'root_directory'):
            path = Path(directory)/'record'
            path.write_bytes(b'accepted')
            path.chmod(0o600)
            before = path.stat(follow_symlinks=False)
            original_fdopen = os.fdopen
            @contextmanager
            def concurrent_change(*args, **kwargs):
                with original_fdopen(*args, **kwargs) as stream:
                    yield stream
                    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns+1))
                    self.assertEqual(path.stat(follow_symlinks=False).st_mtime_ns, before.st_mtime_ns+1)
            with patch.object(a.os, 'fdopen', side_effect=concurrent_change):
                with self.assertRaisesRegex(a.Refusal, 'NATIVE_SERVICE_FILE_CHANGED'):
                    a.protected_read(path)

    def test_real_file_mode_hardlinks_and_symlink_refuse(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(a, 'root_directory'):
            path = Path(directory)/'record'
            path.write_bytes(b'accepted')
            path.chmod(0o600)
            self.assertEqual(a.protected_read(path), b'accepted')
            path.chmod(0o644)
            with self.assertRaises(a.Refusal): a.protected_read(path)
            path.chmod(0o600)
            os.link(path, Path(directory)/'link')
            with self.assertRaises(a.Refusal): a.protected_read(path)
            symlink = Path(directory)/'symlink'
            symlink.symlink_to(path)
            with self.assertRaises(OSError): a.protected_read(symlink)

    def test_world_writable_ancestor_and_symlink_directory_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            writable = Path(directory)/'writable'
            writable.mkdir(mode=0o700)
            writable.chmod(0o777)
            root = writable/'private'
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            with self.assertRaises(a.Refusal): a.root_directory(root, private=True)
            link = root/'link'
            link.symlink_to('/root', target_is_directory=True)
            with self.assertRaises(a.Refusal): a.root_directory(link)


if __name__ == '__main__':
    unittest.main()
