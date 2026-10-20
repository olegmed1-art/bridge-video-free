"""Use actual current-capture fixture output; no alternate capture algorithm."""
import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


a = load('current_anchor_under_test', ROOT / 'ops' / 'light_native_current_anchor.py')
fixtures = load('actual_legacy_capture_fixtures', ROOT / 'tests' / 'test_light_native_legacy_capture.py')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def actual_capture(mode):
    reader = fixtures.Reader(mode)
    # Supply the real full hardening policy to the existing synthetic read port.
    # Source-policy construction is separate from the actual capture algorithm.
    tree = ast.parse((ROOT / 'ops' / 'light_native_service_plan.py').read_text(encoding='utf-8'))
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign) and
                      any(isinstance(target, ast.Name) and target.id == 'HARDENING' for target in node.targets))
    namespace = dict(LIGHT=PurePosixPath('/synthetic'))
    exec(compile(ast.Module(body=[assignment], type_ignores=[]), 'actual_hardening_policy', 'exec'), namespace)
    expected = namespace['HARDENING']
    expected.pop('CPUQuota')
    expected.pop('TimeoutStopSec')
    expected.update(CPUQuotaPerSecUSec='1s', TimeoutStopUSec='30s', MemoryHigh='536870912',
                    MemoryMax='805306368', Restart='on-failure')
    reader.hardening_expected = expected
    reader.hardening_value = deepcopy(expected)
    # Make the shared fixture's unit filename agree with its service name.
    before = reader.layout['base']
    after = '/synthetic/' + reader.layout['unit']
    reader.layout['base'] = after
    reader.service_value['FragmentPath'] = after
    reader.files[after] = reader.files.pop(before)
    raw = fixtures.c._capture_current(reader, runtime_bundle=reader.bundle,
        accepted_runtime_bundle_sha256=reader.bundle['sha256'], controller_source='a' * 40,
        accepted_controller_sha256='b' * 64, clock=lambda: 123456)
    return raw


class AnchorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw_by_mode = {mode: actual_capture(mode) for mode in ('ACTIVE', 'HOLD', 'STOPPED')}

    def setUp(self):
        self.raw = self.raw_by_mode['ACTIVE']
        self.value = json.loads(self.raw)
        self.args = dict(witness_raw=self.raw, accepted_witness_sha256=sha(self.raw),
            expected_controller_source='a' * 40, expected_controller_sha256='b' * 64,
            expected_runtime_bundle_sha256=self.value['runtime_bundle_sha256'],
            original_baseline_sha256='d' * 64, now=123456)

    def encoded(self):
        self.args['witness_raw'] = canonical(self.value)
        self.args['accepted_witness_sha256'] = sha(self.args['witness_raw'])

    def rehash_nested(self):
        self.value['fingerprint_sha256'] = sha(canonical(self.value['fingerprint_inputs']))
        self.value['hardening_sha256'] = sha(canonical(self.value['hardening']))
        self.value['protected_sha256'] = sha(canonical(self.value['protected']))
        self.encoded()

    def unknown(self, **overrides):
        args = {**self.args, **overrides}
        with self.assertRaises(a.AnchorUnknown) as raised:
            a.validate_current_anchor(**args)
        self.assertTrue(str(raised.exception).startswith('CURRENT_ANCHOR_UNKNOWN_'))

    def test_actual_capture_active_hold_and_stopped_are_observation_only(self):
        for mode, raw in self.raw_by_mode.items():
            with self.subTest(mode=mode):
                anchor = a.validate_current_anchor(**{**self.args, 'witness_raw': raw, 'accepted_witness_sha256': sha(raw)})
                self.assertIs(type(anchor), a.CurrentAnchor)
                self.assertEqual(anchor.assurance, 'OBSERVE_ONLY')
                self.assertEqual(anchor.state, 'STOPPED' if mode == 'STOPPED' else 'RUNNING')
                self.assertEqual(anchor.admission, 'ACTIVE' if mode == 'ACTIVE' else 'HOLD')
                self.assertEqual(anchor.original_baseline_sha256, 'd' * 64)
                for name in ('historical_chain_verified', 'historical_baseline_matched', 'continuity_verified',
                             'retirement_authorized', 'admission_authorized', 'new_task_authorized', 'replay_authorized'):
                    self.assertIs(getattr(anchor, name), False)
                with self.assertRaises(FrozenInstanceError):
                    anchor.admission = 'NATIVE'

    def test_explicit_current_observation_gate_and_original_baseline_repin(self):
        anchor = a.validate_current_anchor(**self.args)
        pins = dict(accepted_witness_sha256=anchor.witness_sha256,
                    original_baseline_sha256=anchor.original_baseline_sha256, now=123486)
        self.assertIs(a.require_current_observation(anchor, purpose='OBSERVE_CURRENT', **pins), anchor)
        for purpose in ('OBSERVE', 'RETIREMENT_EVIDENCE', 'RETIRE_PREPARE_WRITE', 'NATIVE_ADMISSION', 'REPLAY', 'ISSUE', 'NO_ABA'):
            with self.subTest(purpose=purpose), self.assertRaises(a.AnchorUnknown):
                a.require_current_observation(anchor, purpose=purpose, **pins)
        for key in ('accepted_witness_sha256', 'original_baseline_sha256'):
            with self.assertRaises(a.AnchorUnknown):
                a.require_current_observation(anchor, purpose='OBSERVE_CURRENT', **{**pins, key: '0' * 64})
        with self.assertRaises(a.AnchorUnknown):
            a.require_current_observation(anchor, purpose='OBSERVE_CURRENT', **{**pins, 'now': 123487})
        with self.assertRaises(a.AnchorUnknown):
            a.require_current_observation(replace(anchor, continuity_verified=True), purpose='OBSERVE_CURRENT', **pins)

    def test_wrong_trusted_integrity_or_source_runtime_pins(self):
        for key, value in (('accepted_witness_sha256', '0' * 64), ('expected_controller_source', '0' * 40),
                           ('expected_controller_sha256', '0' * 64), ('expected_runtime_bundle_sha256', '0' * 64),
                           ('original_baseline_sha256', 'not-a-pin')):
            with self.subTest(key=key):
                self.unknown(**{key: value})

    def test_gate_revalidates_timestamp_state_and_source_projection_from_bytes(self):
        anchor = a.validate_current_anchor(**self.args)
        pins = dict(purpose='OBSERVE_CURRENT', accepted_witness_sha256=anchor.witness_sha256,
                    original_baseline_sha256=anchor.original_baseline_sha256, now=123456)
        changes = (('observed_at', 999999), ('observed_at', 123456.0), ('state', 'not-a-state'),
                   ('state', 'STOPPED'), ('admission', 'HOLD'), ('controller_source', 'e' * 40),
                   ('controller_sha256', 'e' * 64), ('runtime_source', 'e' * 40),
                   ('runtime_bundle_sha256', 'e' * 64), ('_witness_raw', self.raw + b'\n'))
        for key, value in changes:
            with self.subTest(key=key, value=value if key != '_witness_raw' else 'changed bytes'):
                forged = replace(anchor, **{key: value})
                with self.assertRaises(a.AnchorUnknown):
                    a.require_current_observation(forged, **pins)
        # Moving the caller's clock to the forged time cannot renew old bytes.
        forged = replace(anchor, observed_at=999999)
        with self.assertRaises(a.AnchorUnknown):
            a.require_current_observation(forged, **{**pins, 'now': 999999})

    def test_digest_projection_is_json_safe_and_never_contains_witness_bytes(self):
        anchor = a.validate_current_anchor(**self.args)
        projection = a.anchor_projection(anchor)
        encoded = canonical(projection)
        self.assertEqual(projection['witness_sha256'], sha(self.raw))
        self.assertEqual(projection['original_baseline_sha256'], 'd' * 64)
        self.assertEqual(projection['assurance'], 'OBSERVE_ONLY')
        self.assertNotIn('_witness_raw', projection)
        self.assertNotIn('fingerprint_inputs', projection)
        self.assertNotIn('protected_metadata', projection)
        self.assertNotIn(self.raw, encoded)
        self.assertEqual(len(sha(encoded)), 64)
        for key, value in (('observed_at', 999999), ('state', 'not-a-state'), ('controller_source', 'e' * 40)):
            with self.subTest(key=key), self.assertRaises(a.AnchorUnknown):
                a.anchor_projection(replace(anchor, **{key: value}))

    def test_missing_extra_duplicate_noncanonical_or_noninteger_json(self):
        for raw in (self.raw + b'\n', b'{"version":1,"version":1}', b'{"x":NaN}', b'{"x":1.0}', b'\xff', b' ' * 65537):
            with self.subTest(raw=raw[:20]):
                self.unknown(witness_raw=raw, accepted_witness_sha256=sha(raw))
        self.value['unreviewed_field'] = False
        self.encoded()
        self.unknown()
        self.value = json.loads(self.raw)
        self.value.pop('hardening')
        self.encoded()
        self.unknown()

    def test_true_or_numeric_authority_flags_are_refused(self):
        for key in ('historical_chain_verified', 'admission_authorized', 'retirement_authorized'):
            for value in (True, 0, 1):
                self.value = json.loads(self.raw)
                self.value[key] = value
                self.encoded()
                self.unknown()

    def test_freshness_capture_window_future_and_boolean_times(self):
        a.validate_current_anchor(**{**self.args, 'now': 123486})
        for now in (123487, 123455, True):
            self.unknown(now=now)
        for started in (123425, 123457, True):
            self.value = json.loads(self.raw)
            self.value['capture_started_at'] = started
            self.encoded()
            self.unknown()

    def test_installed_release_runtime_source_binding(self):
        for key, value in (('runtime_source', 'e' * 40), ('installed_release', '/synthetic/releases/' + 'e' * 40),
                           ('installed_release', '/synthetic/releases/../' + '1' * 40)):
            self.value = json.loads(self.raw)
            self.value[key] = value
            self.encoded()
            self.unknown()

    def test_hidden_loaded_execstart_user_and_state_changes_are_refused(self):
        for key, value in (('User', 'root'), ('NeedDaemonReload', 'yes'), ('MainPID', '9' * 8000),
                           ('ExecStart', self.value['service']['ExecStart'].replace('worker_v17 ;', 'worker_v17 --extra ;')),
                           ('EnvironmentFiles', '/synthetic/file')):
            with self.subTest(key=key):
                self.value = json.loads(self.raw)
                self.value['service'][key] = value
                self.value['fingerprint_inputs']['service'] = deepcopy(self.value['service'])
                self.rehash_nested()
                self.unknown()

    def test_hashes_bind_full_fingerprint_inputs_protected_and_hardening(self):
        for key in ('fingerprint_sha256', 'protected_sha256', 'hardening_sha256'):
            self.value = json.loads(self.raw)
            self.value[key] = '0' * 64
            self.encoded()
            self.unknown()
        self.value = json.loads(self.raw)
        self.value['fingerprint_inputs']['service']['User'] = 'different'
        self.rehash_nested()
        self.unknown()
        self.value = json.loads(self.raw)
        self.value['fingerprint_inputs']['environment'] = '0' * 64
        self.rehash_nested()
        self.unknown()

    def test_full_hardening_policy_is_checked_even_when_hashes_match(self):
        for key, value in (('NoNewPrivileges', 'no'), ('ProtectSystem', 'no'), ('ReadWritePaths', '/synthetic/other'),
                           ('TasksMax', 'infinity'), ('User', 'root')):
            self.value = json.loads(self.raw)
            self.value['hardening'][key] = value
            self.rehash_nested()
            self.unknown()
        self.value = json.loads(self.raw)
        self.value['hardening'].pop('ProtectControlGroups')
        self.rehash_nested()
        self.unknown()

    def test_root_metadata_owner_type_mode_limit_inode_and_alias(self):
        base = self.value['service']['FragmentPath']
        for index, value in ((2, 1001), (4, stat.S_IFLNK | 0o644), (4, stat.S_IFREG | 0o666),
                              (5, 65537), (1, 0), (2, False)):
            self.value = json.loads(self.raw)
            self.value['protected_metadata'][base][index] = value
            self.encoded()
            self.unknown()
        self.value = json.loads(self.raw)
        rows = list(self.value['protected_metadata'].values())
        rows[1][:2] = rows[0][:2]
        self.encoded()
        self.unknown()

    def test_canonical_drop_and_protected_inventory_are_bound(self):
        drop = self.value['service']['DropInPaths']
        self.value['protected'][drop]['sha256'] = '0' * 64
        self.value['fingerprint_inputs']['drop'] = '0' * 64
        self.rehash_nested()
        self.unknown()
        self.value = json.loads(self.raw)
        self.value['protected']['/synthetic/extra'] = deepcopy(self.value['protected'][drop])
        self.rehash_nested()
        self.unknown()

    def test_actual_process_binding_is_not_a_loaded_mainpid_claim(self):
        for key, value in (('pid', 999), ('uid', 0), ('uids', [0] * 4), ('gids', [1002, 1002, 0, 1002]),
                           ('cwd', '/synthetic/other'), ('start_ticks', 0),
                           ('argv', ['/synthetic/python', '-c', 'unreviewed'])):
            self.value = json.loads(self.raw)
            self.value['process'][key] = value
            self.encoded()
            self.unknown()

    def test_stopped_absence_rejects_populated_foreign_or_live_control_group(self):
        raw = self.raw_by_mode['STOPPED']
        for key, value in (('ControlPID', '101'), ('cgroup_populated', '1'),
                           ('ControlGroup', '/system.slice/other.service'), ('cgroup_present', 0)):
            self.value = json.loads(raw)
            self.value['process'][key] = value
            self.encoded()
            self.unknown()
        self.value = json.loads(raw)
        self.value['admission'] = 'ACTIVE'
        self.encoded()
        self.unknown()


if __name__ == '__main__':
    unittest.main()
