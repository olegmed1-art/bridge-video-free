"""Synthetic contracts; filesystem fixtures require disposable root Linux tmpfs."""
import base64
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
import types
import unittest
from unittest import mock
if sys.platform != 'linux':
    sys.modules.setdefault('fcntl', types.ModuleType('fcntl'))
    sys.modules.setdefault('pwd', types.ModuleType('pwd'))
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('whitelist', HERE / 'collector.py')
c = importlib.util.module_from_spec(spec)
c.PRIVATE_CONFIG_BYTES = (HERE / 'synthetic-config.json').read_bytes()
c.PRIVATE_CONFIG_SHA256 = __import__('hashlib').sha256(c.PRIVATE_CONFIG_BYTES).hexdigest()
spec.loader.exec_module(c)
CFG = c.CFG
c.EXPECTED_PINS = {key: '1' * 64 for key in c.PRIVATE_PIN_KEYS}
c.PINS.update(c.EXPECTED_PINS)

def fixture():
    plan = dict(version=1, source=c.RUNTIME, repository=CFG['identity_29'], target_pr=CFG['target_number'], expected_head_sha='a' * 40, work_key=CFG['identity_30'], objective='SYNTHETIC ONLY', priority=0, branch='fix/synthetic-fixture', task_spec_json={'assignment_schema': CFG['identity_32'], 'repository': CFG['identity_29'], 'target_pr': CFG['target_number'], 'expected_head_sha': 'a' * 40, 'execution_mode': 'READ_ONLY', 'exact_head_binding': True, 'cost_cap_microusd': 0, 'max_repair_attempts': 0, **{k: False for k in c.FALSE_FLAGS}})
    entry = dict(plan_base64=base64.b64encode(c.enc(plan)).decode(), accepted_plan_sha256=c.PLAN)
    previous = dict(plan_sha256='b' * 64, terminal_sha256='c' * 64, sequence=2)
    policy = dict(version=1, action='issue', source='d' * 40, accepted_controller_sha256='e' * 64, accepted_runtime_sha256=c.PINS['runtime-package.json'], predecessor=previous, not_before='2020-01-01T00:00:00Z', expires_at='2020-01-01T01:00:00Z', plans=[entry], authority=dict(owner=CFG['identity_23'], coverage=c.COVERAGE, delegation='FINITE_ISSUER_AGREEMENTS', evidence='synthetic'))
    scope = dict(version=1, operation='native_single_pilot', source=c.RUNTIME, target=c.TARGET, plan_sha256=c.PLAN)
    agreement = dict(version=1, owner=CFG['identity_23'], operation_digest=c.sha(c.enc(scope)), not_before='2020-01-01T00:00:00Z', expires_at='2020-01-01T00:30:00Z', coverage=c.COVERAGE, evidence=f'Explicit finite issuer policy {c.POLICY}; catalogue entry 0. No unlisted work, renewal, repair, spending or unknown-outcome replay.')
    prepare = dict(version=2, action='prepare', source=policy['source'], accepted_controller_sha256=policy['accepted_controller_sha256'], accepted_runtime_sha256=policy['accepted_runtime_sha256'], **entry, agreement=agreement, accepted_agreement_sha256=c.sha(c.enc(agreement)), accepted_receipt_sha256=None, accepted_discovery_sha256=None, accepted_permit_sha256=None, accepted_terminal_sha256=None, predecessor=previous)
    cycle = dict(version=1, action='cycle', prepare=prepare)
    issuer = dict(start=c.stamp('2020-01-01T00:00:00Z'), cycle=cycle)
    baseline = dict(version=1, source=c.RUNTIME, package_sha256=policy['accepted_runtime_sha256'], agreement_sha256=prepare['accepted_agreement_sha256'], scope_sha256=c.sha(c.enc(scope)))
    return [policy, issuer, cycle, prepare, plan, baseline]

class Contracts(unittest.TestCase):

    def test_expired_historical_derivation_only(self):
        self.assertEqual(c.bindings(*fixture())['sequence'], 2)

    def test_predecessor_scope_and_traversal(self):
        for field, bad in [('sequence', 3), ('sequence', True), ('plan_sha256', '../secret'), ('plan_sha256', c.PLAN), ('terminal_sha256', 'bad')]:
            f = fixture()
            f[0]['predecessor'][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(Exception):
                c.bindings(*f)

    def test_each_prepare_binding_changed(self):
        for key in fixture()[3]:
            f = copy.deepcopy(fixture())
            f[3][key] = 'CHANGED'
            with self.subTest(key=key), self.assertRaises(Exception):
                c.bindings(*f)

    def test_policy_authority(self):
        for key in fixture()[0]['authority']:
            f = fixture()
            f[0]['authority'][key] = None
            with self.subTest(key=key), self.assertRaises(Exception):
                c.bindings(*f)

    def test_no_budget_or_write_scope(self):
        for key in (*c.FALSE_FLAGS, 'cost_cap_microusd', 'max_repair_attempts'):
            f = fixture()
            f[4]['task_spec_json'][key] = True
            f[0]['plans'][0]['plan_base64'] = base64.b64encode(c.enc(f[4])).decode()
            f[3]['plan_base64'] = f[0]['plans'][0]['plan_base64']
            with self.subTest(key=key), self.assertRaises(Exception):
                c.bindings(*f)

    def test_wrong_target_and_branch(self):
        for key, bad in [('target_pr', 1), ('work_key', 'other'), ('branch', '../../secret'), ('source', 'f' * 40), ('expected_head_sha', 'x')]:
            f = fixture()
            f[4][key] = bad
            f[0]['plans'][0]['plan_base64'] = base64.b64encode(c.enc(f[4])).decode()
            f[3]['plan_base64'] = f[0]['plans'][0]['plan_base64']
            with self.subTest(key=key), self.assertRaises(Exception):
                c.bindings(*f)

    def test_no_agreement_renewal(self):
        f = fixture()
        f[3]['agreement']['expires_at'] = '2099-01-01T00:00:00Z'
        with self.assertRaises(Exception):
            c.bindings(*f)

    def test_baseline_drift(self):
        for key in fixture()[5]:
            f = fixture()
            f[5][key] = None
            with self.subTest(key=key), self.assertRaises(Exception):
                c.bindings(*f)

    def test_json_canonical_and_duplicate(self):
        for raw in (b'{"x":1,"x":1}', b'{"x":NaN}', b'{"x": 1}', b'[]'):
            with self.subTest(raw=raw), self.assertRaises(Exception):
                c.parse(raw)
        self.assertEqual(c.parse(b'{"x":"\\u0430"}', True), {'x': 'а'})

    def test_digest_mismatch_no_parsing(self):
        reader = mock.Mock()
        reader.read.return_value = b'PRIVATE_SENTINEL'
        with self.assertRaises(c.Refused):
            c.pinned(reader, '/fixed', 'plan.json')

    def test_wrong_identity_no_reader(self):
        with mock.patch.object(c.sys, 'platform', 'wrong'), mock.patch.object(c, 'Reader') as reader:
            with self.assertRaises(Exception):
                c.collect()
            reader.assert_not_called()

    def test_missing_private_pins_refuses_before_host(self):
        with mock.patch.object(c, 'EXPECTED_PINS', None), mock.patch.object(c, 'collect') as collect, mock.patch.object(c.sys, 'argv', ['collector']), mock.patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(c.main(), 2)
            collect.assert_not_called()

    def test_exception_text_never_output(self):
        with mock.patch.object(c, 'collect', side_effect=ValueError('PRIVATE_SENTINEL')), mock.patch.object(c.signal, 'signal'), mock.patch.object(c.signal, 'alarm', create=True), mock.patch.object(c.sys, 'argv', ['collector']), mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(c.main(), 2)
            self.assertEqual(json.loads(output.getvalue()), dict(kind=CFG['identity_35'], state='REFUSED', stage='START', issue_allowed=False))

    def test_proc_hold_refuses_run_and_duplicate(self):
        row = {'MainPID': '123'}
        for raw in ((CFG['identity_24']+'=RUN\0').encode(), (CFG['identity_24']+'=HOLD\0'+CFG['identity_24']+'=RUN\0').encode()):
            with mock.patch.object(c.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=42), create=True), mock.patch.object(c.os, 'stat', return_value=types.SimpleNamespace(st_uid=42)), mock.patch.object(c.os, 'readlink', return_value='/release'), mock.patch.object(c, 'proc', return_value=raw):
                with self.assertRaises(Exception):
                    c.process_hold(row, '/release')

    def test_legacy_release_path_injection(self):
        baseline = {'prior': dict(hostname=CFG['identity_33'], pid=123, invocation_id='f' * 32, release='/tmp/secret', fingerprint='a' * 64)}
        reader = mock.Mock()
        with self.assertRaises(Exception):
            c.legacy_hold(reader, baseline)
        reader.read.assert_not_called()

    def test_legacy_exact_fingerprint_and_protected_drift(self):
        release = c.LIGHT + '/releases/' + 'a' * 40
        unit = '/etc/systemd/system/' + c.LEGACY
        drop = unit + CFG['identity_21']
        disk = CFG['identity_15']
        pins = release + CFG['identity_22']
        route = CFG['identity_16']
        expected = {unit: (420, 65536), drop: (420, 4096), disk: (384, 262144), pins: (292, 4096), route: (420, 4096)}
        data = {path: ('SYNTHETIC_PRIVATE_SENTINEL_' + str(i)).encode() for i, path in enumerate(expected)}
        protected = {path: dict(mode=m, limit=l, sha256=c.sha(data[path])) for path, (m, l) in expected.items()}
        row = dict(ActiveState='active', SubState='running', MainPID='123', NRestarts='0', InvocationID='a' * 32, WorkingDirectory=release, User=CFG['identity_14'], Group=CFG['identity_14'], Environment=CFG['identity_24']+'=HOLD', EnvironmentFiles=[disk + ' (ignore_errors=no)', pins + ' (ignore_errors=no)'], ExecStart='synthetic', FragmentPath=unit, DropInPaths=drop, NeedDaemonReload='no')
        fp = c.sha(c.enc(dict(service=row, environment=c.sha(data[disk]), pins=c.sha(data[pins]), route=c.sha(data[route]), drop=c.sha(data[drop]))))
        baseline = dict(prior=dict(hostname=CFG['identity_33'], pid=123, invocation_id='a' * 32, release=release, fingerprint=fp), protected=protected, protected_sha256=c.sha(c.enc(protected)))
        reader = mock.Mock()
        reader.read.side_effect = lambda path, *a, **k: data[path]
        with mock.patch.object(c, 'show', return_value=row), mock.patch.object(c, 'process_hold'):
            self.assertEqual(c.legacy_hold(reader, baseline), row)
            data[disk] = b'CHANGED_PRIVATE_SENTINEL'
            with self.assertRaises(Exception):
                c.legacy_hold(reader, baseline)

    def test_native_admission_refuses_before_history(self):
        reader = mock.Mock()
        reader.names.return_value = {'admission', 'current.json', 'jobs'}
        reader.read.return_value = b'RUN\n'
        with mock.patch.object(c.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_gid=42), create=True):
            with self.assertRaises(c.Refused):
                c.native_hold(reader, {}, {}, {})
        self.assertEqual(reader.read.call_count, 1)

    def test_predecessor_digest_refuses_before_records(self):
        reader = mock.Mock()
        reader.read.return_value = b'SYNTHETIC_WRONG_PREDECESSOR'
        with self.assertRaises(c.Refused):
            c.predecessor(reader, fixture()[0]['predecessor'], fixture()[3])
        self.assertEqual(reader.read.call_count, 1)

    def test_process_no_new_privileges_required(self):

        def fake(pid, name):
            return (CFG['identity_24']+'=HOLD\0').encode() if name == 'environ' else b'NoNewPrivs:\t0\n'
        with mock.patch.object(c.pwd, 'getpwnam', return_value=types.SimpleNamespace(pw_uid=42), create=True), mock.patch.object(c.os, 'stat', return_value=types.SimpleNamespace(st_uid=42)), mock.patch.object(c.os, 'readlink', return_value='/release'), mock.patch.object(c, 'proc', side_effect=fake):
            with self.assertRaises(c.Refused):
                c.process_hold({'MainPID': '123'}, '/release')

@unittest.skipUnless(os.environ.get('WHITELIST_FIXTURE') == '1', 'Linux root tmpfs fixtures only')
class Filesystem(unittest.TestCase):

    def setUp(self):
        need = sys.platform == 'linux' and os.getuid() == 0 and os.path.ismount('/fixture-data')
        if not need:
            raise RuntimeError('NOT_ISOLATED_ROOT_FIXTURE')
        import tempfile
        self.path = Path(tempfile.mkdtemp(dir='/fixture-data'))
        self.path.chmod(448)
        self.file = self.path / 'record'
        self.file.write_bytes(b'synthetic')
        self.file.chmod(384)
        self.reader = c.Reader()

    def tearDown(self):
        self.reader.close()
        import shutil
        shutil.rmtree(self.path)

    def test_read_no_atime(self):
        before = self.file.stat()
        self.assertEqual(self.reader.read(str(self.file)), b'synthetic')
        self.reader.recheck()
        after = self.file.stat()
        self.assertEqual(before, after)

    def test_wrong_mode(self):
        self.file.chmod(420)
        with self.assertRaises(Exception):
            self.reader.read(str(self.file))

    def test_wrong_owner(self):
        os.chown(self.file, 65534, 65534)
        with self.assertRaises(Exception):
            self.reader.read(str(self.file))

    def test_parent_permission_drift(self):
        self.reader.read(str(self.file))
        self.path.chmod(511)
        with self.assertRaises(Exception):
            self.reader.recheck()

    def test_size_limit(self):
        with self.assertRaises(Exception):
            self.reader.read(str(self.file), limit=1)

    def test_symlink(self):
        link = self.path / 'link'
        link.symlink_to(self.file)
        with self.assertRaises(Exception):
            self.reader.read(str(link))

    def test_symlink_parent(self):
        real = self.path / 'real'
        real.mkdir(mode=448)
        (real / 'leaf').write_bytes(b'x')
        (real / 'leaf').chmod(384)
        link = self.path / 'link'
        link.symlink_to(real)
        with self.assertRaises(Exception):
            self.reader.read(str(link / 'leaf'))

    def test_hardlink(self):
        os.link(self.file, self.path / 'alias')
        with self.assertRaises(Exception):
            self.reader.read(str(self.file))

    def test_fifo(self):
        fifo = self.path / 'fifo'
        os.mkfifo(fifo, 384)
        with self.assertRaises(Exception):
            self.reader.read(str(fifo))

    def test_content_drift(self):
        self.reader.read(str(self.file))
        self.file.write_bytes(b'changed!!')
        with self.assertRaises(Exception):
            self.reader.recheck()

    def test_replacement(self):
        self.reader.read(str(self.file))
        self.file.unlink()
        self.file.write_bytes(b'synthetic')
        self.file.chmod(384)
        with self.assertRaises(Exception):
            self.reader.recheck()

    def test_inventory_drift(self):
        self.reader.names(str(self.path))
        (self.path / 'extra').write_bytes(b'x')
        with self.assertRaises(Exception):
            self.reader.recheck()

    def test_missing_lock_never_created(self):
        lock = self.path / 'missing'
        with self.assertRaises(Exception):
            self.reader.lock(str(lock))
        self.assertFalse(lock.exists())

    def test_busy_lock(self):
        import fcntl
        with self.file.open('rb') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(Exception):
                self.reader.lock(str(self.file))

    def test_traversal(self):
        with self.assertRaises(Exception):
            self.reader.directory(str(self.path) + '/../elsewhere')
if __name__ == '__main__':
    unittest.main(verbosity=2)
