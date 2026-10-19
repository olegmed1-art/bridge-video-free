"""Read-only reconciliation contracts; synthetic ports never prove production.

Portable flow cases execute the actual inventory, Agreement class, capture and
anchor algorithms, with synthetic installed packages and trusted read ports.
Linux-root cases exercise real Snapshot/flock/local refusal separately.
"""
import ast
import base64
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops import light_native_reconciliation as q
from tests.test_light_native_retirement_inventory import fixture as inventory_fixture
from tests import test_light_native_current_anchor as anchor_fixtures

ROOT = Path(__file__).resolve().parents[1]


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def seal(value):
    value['agreement']['operation_digest'] = q.sha(q.encoded(q.scope(value)))
    value['accepted_agreement_sha256'] = q.sha(q.encoded(value['agreement']))
    return q.encoded(value)


def reference_value(now=None):
    now = int(time.time()) if now is None else int(now)
    value = dict(version=1, action='observe-current-reconciliation-reference', source='a'*40,
        accepted_controller_sha256='b'*64, accepted_runtime_sha256='c'*64,
        accepted_capture_helpers_sha256='d'*64, accepted_legacy_package_sha256='e'*64,
        legacy_source=q.LEGACY_SOURCE, legacy_bundle_sha256=q.LEGACY_BUNDLE,
        policy_sha256='f'*64, index=0, original_inventory_sha256='1'*64, original_baseline_sha256='2'*64,
        agreement=dict(version=1, owner='olegmed1-art', operation_digest='0'*64,
            not_before=stamp(now-10), expires_at=stamp(now+1200), coverage=q.COVERAGE,
            evidence='OWNER_ACCEPTED_READ_ONLY_CURRENT_RECONCILIATION'))
    seal(value)
    return value


def actual_agreement_class():
    # Execute the existing class, avoiding its unrelated fcntl-only module import
    # on Windows. The hash/require operations are the existing retirement helpers.
    tree = ast.parse((ROOT/'ops/native_maintenance_agreement.py').read_bytes())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Agreement')
    namespace = dict(datetime=datetime, timezone=timezone, json=json, re=re, time=time,
        COVERAGE=q.COVERAGE, digest=lambda obj: q.sha(q.encoded(obj)), require=q.r.need)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'actual-Agreement', 'exec'), namespace)
    return namespace['Agreement']


def retained_fixture(runtime):
    view, old_args, _, old_plan = inventory_fixture()
    old_policy = old_args[0]
    policy = q.r.parse(view.files['issuers/'+old_policy+'/policy.json'])
    plan = q.r.parse(view.files[old_plan+'/plan.json'])
    plan['source'] = q.NATIVE_SOURCE
    plan_raw = q.encoded(plan); plan_pin = q.sha(plan_raw)
    plan_entry = dict(plan_base64=base64.b64encode(plan_raw).decode(), accepted_plan_sha256=plan_pin)
    policy.update(accepted_runtime_sha256=q.sha(runtime), plans=[plan_entry])
    policy_raw = q.encoded(policy); policy_pin = q.sha(policy_raw)
    view.files = {name.replace(old_plan, plan_pin).replace(old_policy, policy_pin): raw
                  for name, raw in view.files.items()}
    entry = 'issuers/'+policy_pin+'/0000'
    prepare = dict(source=policy['source'], accepted_controller_sha256=policy['accepted_controller_sha256'],
        accepted_runtime_sha256=q.sha(runtime), predecessor=policy['predecessor'], **plan_entry)
    cycle = dict(version=1, action='cycle', prepare=prepare)
    view.files.update({plan_pin+'/plan.json': plan_raw, plan_pin+'/runtime-package.json': runtime,
        'issuers/'+policy_pin+'/policy.json': policy_raw, 'cycles/'+plan_pin+'/intent.json': q.encoded(cycle),
        entry+'/intent.json': q.encoded(dict(start=1, cycle=cycle))})
    return view, policy_pin, plan_pin


@contextmanager
def synthetic_flow(*, runtime_ascii=True):
    now = int(time.time())
    clock = SimpleNamespace(now=float(now))
    hooks = SimpleNamespace(lock_exit=lambda: None, snapshot_exit=lambda: None)
    controller = b'synthetic-controller-not-production'
    # Actual retained release.package uses ASCII JSON, including escaped source
    # text. A UTF-8 canonical re-encoding would alter its historical digest.
    runtime_bundle = dict(revision=q.NATIVE_SOURCE,
                          files={'oracle_autopilot/synthetic.py': '# Пример\n'})
    native_encoded = lambda value: json.dumps(value, sort_keys=True,
        separators=(',', ':'), ensure_ascii=runtime_ascii).encode()
    runtime_bundle['sha256'] = q.sha(native_encoded(runtime_bundle))
    runtime = native_encoded(dict(version=1, source=q.NATIVE_SOURCE, runtime=runtime_bundle, helpers={}))
    view, policy_pin, plan_pin = retained_fixture(runtime)
    before = deepcopy(view.files)
    inventory, proposed = q.r._retained_issuer_inputs(view, policy_pin, 0, q.NATIVE_SOURCE, q.sha(runtime))
    captured_reader = []
    original_capture = anchor_fixtures.fixtures.c._capture_current
    def obtain_reader(reader, **kwargs):
        captured_reader.append(reader)
        return original_capture(reader, **kwargs)
    with patch.object(anchor_fixtures.fixtures.c, '_capture_current', side_effect=obtain_reader):
        anchor_fixtures.actual_capture('ACTIVE')
    reader = captured_reader[0]
    legacy = reader.bundle
    legacy_raw = q._legacy_encoded(legacy)
    helpers = q.encoded(dict(version=1, kind='CURRENT_RECONCILIATION_HELPERS', source='a'*40,
                            helpers=dict.fromkeys(q.HELPERS, '# synthetic package only\n')))
    events = []
    @contextmanager
    def locks(root):
        events.append('locks-enter')
        try: yield
        finally:
            events.append('locks-exit'); hooks.lock_exit()
    @contextmanager
    def snapshot(root, output):
        events.append('snapshot-enter')
        try: yield view
        finally:
            events.append('snapshot-exit'); hooks.snapshot_exit()
    def local(actual_view, proposal, pins, *, observation):
        events.append('original-local')
        if actual_view is not view or observation is not True:
            raise AssertionError('wrong local binding')
        if pins != inventory['journal_sha256'] or proposal['historical_controller_source'] != '4'*40:
            raise AssertionError('original history was replaced')
        return dict(baseline=q.r.parse(view.files[plan_pin+'/baseline.json']))
    def capture(**kwargs):
        events.append('current-capture')
        return original_capture(reader, **kwargs, clock=lambda: time.time())
    owner = SimpleNamespace(ROOT=Path('/synthetic-owner'), install=SimpleNamespace(RETAINED_SOURCE=q.NATIVE_SOURCE),
        validate_package=lambda *args: events.append('controller-validated'),
        release=SimpleNamespace(validate=lambda *args: events.append('native-package-validated'),
            staging=SimpleNamespace(require_current_main=lambda source: events.append('main'))))
    live = SimpleNamespace(observation_locks=locks, local=local)
    bounded = SimpleNamespace(run=lambda operation: operation())
    capture_module = SimpleNamespace(capture_current_legacy=capture)
    current = anchor_fixtures.a
    guard = SimpleNamespace(assert_current=lambda: events.append('guard'))
    # Production source/bundle constants remain fixed in source. Only synthetic
    # fixture input pins are selected here; this proves no production acceptance.
    with patch.object(q, 'LEGACY_SOURCE', legacy['revision']), patch.object(q, 'LEGACY_BUNDLE', legacy['sha256']), \
         patch.object(q, '_components', return_value=(owner, live, bounded, actual_agreement_class(), capture_module, current)), \
         patch.object(q.r, 'Snapshot', side_effect=snapshot), \
         patch.object(q.time, 'time', side_effect=lambda: clock.now), \
         patch.object(q.os, 'geteuid', return_value=0, create=True), \
         patch.object(q.os, 'uname', return_value=SimpleNamespace(nodename='autopilot-lite-vnic'), create=True):
        value = reference_value(now)
        value.update(accepted_controller_sha256=q.sha(controller), accepted_runtime_sha256=q.sha(runtime),
            accepted_capture_helpers_sha256=q.sha(helpers), accepted_legacy_package_sha256=q.sha(legacy_raw),
            policy_sha256=policy_pin, original_inventory_sha256=q.sha(q.encoded(inventory)),
            original_baseline_sha256=inventory['journal_sha256']['baseline.json'])
        raw = seal(value)
        def observe():
            request = seal(value)
            return q.observe_current(controller, runtime, helpers, legacy_raw, request, q.sha(request), guard)
        yield SimpleNamespace(value=value, raw=raw, observe=observe, view=view, original=before,
            inventory=inventory, proposed=proposed, plan_pin=plan_pin, events=events, owner=owner, live=live,
            bounded=bounded, capture=capture_module, current=current, controller=controller, runtime=runtime,
            helpers=helpers, legacy=legacy, legacy_raw=legacy_raw, guard=guard, clock=clock, hooks=hooks)


class ReferenceTests(unittest.TestCase):
    def test_exact_reference_binds_every_blob_source_selector_and_original_pin(self):
        value = reference_value(); raw = seal(value)
        self.assertEqual(q.reference(raw, q.sha(raw)), value)
        for key in q.scope(value):
            if key in ('version', 'operation'): continue
            changed = deepcopy(value)
            changed[key] = 1 if key == 'index' else '0'*(40 if key in ('source', 'legacy_source') else 64)
            with self.subTest(key=key), self.assertRaises(q.Refusal): q.reference(q.encoded(changed))

    def test_shape_canonical_hash_and_finite_agreement_are_strict(self):
        original = reference_value()
        for fault in ('unknown', 'version', 'index', 'owner', 'coverage', 'evidence', 'long', 'zero', 'hash', 'action'):
            value = deepcopy(original)
            if fault == 'unknown': value['credential'] = 'PRIVATE_SENTINEL'
            if fault == 'version': value['version'] = True
            if fault == 'index': value['index'] = True
            if fault == 'owner': value['agreement']['owner'] = 'someone-else'
            if fault == 'coverage': value['agreement']['coverage'] = []
            if fault == 'evidence': value['agreement']['evidence'] = 'RETIRE_AND_ADMIT'
            if fault == 'long': value['agreement']['expires_at'] = stamp(time.time()+90000)
            if fault == 'zero': value['agreement']['expires_at'] = value['agreement']['not_before']
            if fault == 'action': value['action'] = 'retire-prepare-reference'
            raw = seal(value)
            if fault == 'hash': value['accepted_agreement_sha256'] = '0'*64; raw = q.encoded(value)
            with self.subTest(fault=fault), self.assertRaises(q.Refusal): q.reference(raw)
        for raw in (seal(original)+b' ', b'{"version":1,"version":1}', b'{"version":NaN}'):
            with self.assertRaises((q.Refusal, RuntimeError)): q.reference(raw)
        with self.assertRaises(q.Refusal): q.reference(seal(original), '0'*64)

    def test_helpers_exact_four_fixed_paths_kind_source_and_hash(self):
        value = dict(version=1, kind='CURRENT_RECONCILIATION_HELPERS', source='a'*40,
                     helpers=dict.fromkeys(q.HELPERS, '# fixture\n'))
        raw = q.encoded(value)
        self.assertEqual(q.validate_helpers(raw, q.sha(raw), 'a'*40), value)
        for fault in ('extra', 'missing', 'path', 'source', 'kind', 'version', 'text'):
            changed = deepcopy(value)
            if fault == 'extra': changed['credential'] = 'PRIVATE_SENTINEL'
            if fault == 'missing': changed['helpers'].pop(next(iter(q.HELPERS)))
            if fault == 'path': changed['helpers']['../arbitrary.py'] = '# unsafe'
            if fault == 'source': changed['source'] = 'b'*40
            if fault == 'kind': changed['kind'] = 'ANOTHER_PACKAGE'
            if fault == 'version': changed['version'] = True
            if fault == 'text': changed['helpers'][next(iter(q.HELPERS))] = None
            raw = q.encoded(changed)
            with self.subTest(fault=fault), self.assertRaises(q.Refusal): q.validate_helpers(raw, q.sha(raw), 'a'*40)

    def test_legacy_bundle_default_ascii_canonical_logical_and_wire_pins(self):
        value = dict(revision=q.LEGACY_SOURCE, files={'oracle_autopilot/worker_v17.py': '# fixture \u044f\n',
                                                   'ops/autopilot/broker-hold.env': 'FIXTURE=value\n'})
        value['sha256'] = q.sha(q._legacy_encoded(value))
        raw = q._legacy_encoded(value)
        self.assertEqual(q.validate_legacy_package(raw, q.sha(raw), value['sha256'], q.LEGACY_SOURCE), value)
        for fault in ('wire', 'logical', 'revision', 'path', 'absolute', 'utf8', 'extra'):
            changed = deepcopy(value)
            if fault == 'path': changed['files']['ops/../../unsafe.py'] = '# unsafe'
            if fault == 'absolute': changed['files']['/etc/environment'] = 'unsafe'
            if fault == 'extra': changed['command'] = 'unsafe'
            blob = q.encoded(changed) if fault == 'utf8' else q._legacy_encoded(changed)
            with self.subTest(fault=fault), self.assertRaises(q.Refusal):
                q.validate_legacy_package(blob, '0'*64 if fault == 'wire' else q.sha(blob),
                    '0'*64 if fault == 'logical' else value['sha256'], '0'*40 if fault == 'revision' else q.LEGACY_SOURCE)


class CallbackBoundaryTests(unittest.TestCase):
    def test_retained_runtime_ascii_source_encoding_is_preserved_through_full_flow(self):
        with synthetic_flow() as f:
            self.assertIn(b'\\u041f', f.runtime)
            self.assertNotEqual(f.runtime, q.encoded(q.r.parse(f.runtime)))
            with self.assertRaises(q.Refusal): q._parse(f.runtime, limit=3*1024*1024)
            self.assertEqual(f.observe()['state'], 'OBSERVED_CURRENT')
        with synthetic_flow(runtime_ascii=False) as f:
            value = f.observe()
            self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'PACKAGES'))
            self.assertNotIn('locks-enter', f.events)
            self.assertEqual(f.view.files, f.original)

    def test_current_success_keeps_original_bytes_and_every_authority_flag_false(self):
        with synthetic_flow() as f:
            value = f.observe()
            self.assertEqual(value['state'], 'OBSERVED_CURRENT')
            q.public_result(value, f.value)
            self.assertTrue(value['local_journals_verified']); self.assertTrue(value['current_snapshot_verified'])
            self.assertTrue(all(value[k] is False for k in q.FALSE_FLAGS))
            self.assertEqual(f.view.files, f.original)
            self.assertLess(f.events.index('original-local'), f.events.index('current-capture'))
            self.assertLessEqual(len(q.encoded(value)), 4096)
            for private in (b'PRIVATE_SENTINEL', b'MainPID', b'admission', b'work_key', b'ACTIVE'):
                if private == b'admission': continue  # The required false authority flag contains this word.
                self.assertNotIn(private, q.encoded(value))

    def test_invalid_real_clock_authority_prevents_all_protected_reads(self):
        for fault in ('expired', 'future'):
            with self.subTest(fault=fault), synthetic_flow() as f:
                now = time.time()
                f.value['agreement'].update(not_before=stamp(now-1000 if fault == 'expired' else now+100),
                    expires_at=stamp(now-1 if fault == 'expired' else now+1000))
                f.view.accessed.clear(); f.events.clear()
                value = f.observe()
                self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'AUTHORITY'))
                self.assertEqual(f.view.accessed, [])
                self.assertNotIn('locks-enter', f.events)

    def test_original_inventory_and_baseline_pins_cannot_be_recomputed_or_substituted(self):
        for key in ('original_inventory_sha256', 'original_baseline_sha256'):
            with self.subTest(key=key), synthetic_flow() as f:
                f.value[key] = '0'*64
                value = f.observe()
                self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'ORIGINAL_INVENTORY'))
                self.assertNotIn('original-local', f.events); self.assertNotIn('current-capture', f.events)

    def test_failed_original_local_guard_is_never_bypassed_with_current_capture(self):
        with synthetic_flow() as f:
            def refused(*args, **kwargs): raise RuntimeError('PRIVATE_SENTINEL old source guard')
            f.live.local = refused
            value = f.observe()
            self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'LOCAL_JOURNALS'))
            self.assertNotIn('current-capture', f.events)
            self.assertNotIn(b'PRIVATE_SENTINEL', q.encoded(value))
            self.assertTrue(all(value[k] is False for k in q.FALSE_FLAGS))

    def test_snapshot_drift_and_final_guard_expiry_or_staleness_cannot_return_observed(self):
        for fault in ('snapshot', 'expiry', 'stale-anchor', 'rollback', 'wrong-runtime'):
            with self.subTest(fault=fault), synthetic_flow() as f:
                original = f.owner.release.staging.require_current_main
                calls = []
                def final(source):
                    calls.append(source); original(source)
                    if len(calls) == 2:
                        if fault == 'snapshot': f.view.drift = True
                        elif fault == 'expiry': f.clock.now += 1300
                        elif fault == 'stale-anchor': f.clock.now += 31
                        elif fault == 'rollback': f.clock.now -= 1
                f.owner.release.staging.require_current_main = final
                if fault == 'wrong-runtime':
                    original_capture = f.capture.capture_current_legacy
                    def wrong(**kwargs):
                        raw = original_capture(**kwargs); value = json.loads(raw)
                        value['runtime_source'] = '0'*40
                        return q._legacy_encoded(value)
                    f.capture.capture_current_legacy = wrong
                value = f.observe()
                self.assertEqual(value['state'], 'UNKNOWN')
                self.assertFalse(value['local_journals_verified']); self.assertFalse(value['current_snapshot_verified'])
                self.assertIsNone(value['current_anchor_sha256'])

    def test_expiry_and_stale_current_anchor_during_context_exit_stay_unknown(self):
        for boundary in ('lock_exit', 'snapshot_exit'):
            for seconds in (31, 1300):
                with self.subTest(boundary=boundary, seconds=seconds), synthetic_flow() as f:
                    def elapsed(): f.clock.now += seconds
                    setattr(f.hooks, boundary, elapsed)
                    value = f.observe()
                    self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'FINAL_GUARDS'))
                    self.assertFalse(value['local_journals_verified'])
                    self.assertFalse(value['current_snapshot_verified'])
                    self.assertIsNone(value['current_anchor_sha256'])

    def test_host_or_unaccepted_helpers_stop_before_components_and_reads(self):
        with synthetic_flow() as f:
            f.view.accessed.clear()
            q.os.geteuid.return_value = 1234
            value = f.observe()
            self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'HOST'))
            self.assertEqual(f.view.accessed, [])
        with synthetic_flow() as f:
            f.view.accessed.clear(); f.events.clear()
            f.value['accepted_capture_helpers_sha256'] = '0'*64
            value = f.observe()
            self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'PACKAGES'))
            self.assertNotIn('controller-validated', f.events)
            self.assertEqual(f.view.accessed, [])

    def test_bounded_unknown_remains_unknown_and_exposes_no_exception_text(self):
        with synthetic_flow() as f:
            def unknown(operation): raise RuntimeError('PRIVATE_SENTINEL')
            f.bounded.run = unknown
            value = f.observe()
            self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'BOUNDED_UNKNOWN'))
            self.assertNotIn('locks-enter', f.events)
            self.assertNotIn(b'PRIVATE_SENTINEL', q.encoded(value))

    def test_public_reply_cannot_promote_authority_or_swap_valid_original_pins(self):
        with synthetic_flow() as f:
            value = f.observe()
            for key in (*q.FALSE_FLAGS, 'original_baseline_sha256', 'original_inventory_sha256', 'extra'):
                changed = deepcopy(value)
                changed[key] = True if key in q.FALSE_FLAGS else '0'*64
                with self.subTest(key=key), self.assertRaises(q.Refusal): q.public_result(changed, f.value)
            unknown = q._result(f.value, reason='BOUNDED_UNKNOWN')
            q.public_result(unknown, f.value)
            unknown['original_baseline_sha256'] = '0'*64
            with self.assertRaises(q.Refusal): q.public_result(unknown, f.value)


@unittest.skipUnless(sys.platform == 'linux' and getattr(os, 'geteuid', lambda: 1)() == 0,
                     'NOTRUN: actual root Snapshot/flock/local composition requires Linux root')
class LinuxOriginalGuardEvidence(unittest.TestCase):
    def test_actual_original_local_guard_refuses_bad_history_before_current_capture(self):
        from ops import light_native_lane_controller as owner
        from ops import light_native_retirement_live as live
        from ops import native_maintenance_agreement as agreement
        from ops import light_native_legacy_capture as capture
        from ops import light_native_current_anchor as current
        from ops import light_native_retirement as real_r
        original_snapshot = real_r.Snapshot
        with synthetic_flow() as f, tempfile.TemporaryDirectory(prefix='current-reconciliation-', dir='/root') as directory:
            root = Path(directory); root.chmod(0o700)
            f.view.files.update({'issuers/cycle.lock': b'', 'cycles/cycle.lock': b''})
            for relative, raw in f.view.files.items():
                path = root/relative
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                # Normalize every synthetic evidence directory to private mode.
                for parent in path.parents:
                    if parent == root.parent: break
                    parent.chmod(0o700)
                path.write_bytes(raw); path.chmod(0o600)
            before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            # Package bytes are synthetic: their provenance validators are
            # explicitly stubbed. Snapshot, flock, Agreement and the original
            # local history algorithm below remain real Linux implementations.
            with patch.object(owner, 'ROOT', root), patch.object(owner, 'validate_package'), \
                 patch.object(owner.release, 'validate') as package_validation, \
                 patch.object(owner.release.staging, 'require_current_main'), \
                 patch.object(q.r, 'Snapshot', original_snapshot), \
                 patch.object(live, 'local', wraps=live.local) as original_local, \
                 patch.object(q, '_components', return_value=(owner, live, f.bounded, agreement.Agreement, capture, current)), \
                 patch.object(capture, 'capture_current_legacy', side_effect=AssertionError('must not capture')) as current_read:
                value = f.observe()
                self.assertEqual((value['state'], value['reason']), ('UNKNOWN', 'LOCAL_JOURNALS'))
                package_validation.assert_called_once()
                original_local.assert_called_once()
                self.assertIs(original_local.call_args.kwargs['observation'], True)
                self.assertIsInstance(original_local.call_args.args[0], original_snapshot)
                current_read.assert_not_called()
            self.assertEqual(before, {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
