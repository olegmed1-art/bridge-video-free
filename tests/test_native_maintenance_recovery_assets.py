"""Data-only restore, semantic delta and simulated OCI failure contracts."""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

from database import native_cli_permission_engine as engine
from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_recovery_assets as assets
from ops import native_maintenance_recovery_assets_runner as runner
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_owner_attest as owner
from ops.native_permission_hold_guard import EXPECTED_TARGET
from test_native_maintenance_checkpoint_oci import FakeClient, ServiceError


def fixture():
    source = 'a' * 40
    source_data = bundle.canonical(dict(version=1, source_sha=source,
        files={p: base64.b64encode(b'raise RuntimeError("MUST NEVER EXECUTE")\n').decode()
               for p in bundle.FILES}))
    signatures = (*engine.FUNCTIONS, engine.HELPER)
    before = dict(target=copy.deepcopy(EXPECTED_TARGET), config=[dict(enabled=False)], receipts=0,
        nonterminal_tasks=0, owner_oid=42, recipient_oid=43,
        function_ids={sig: n for n, sig in enumerate(signatures, 100)},
        functions=[dict(oid=n, name=sig.split('.')[1].split('(')[0], owner=42, acl=[])
                   for n, sig in enumerate(signatures, 100)])
    manifest = engine.encode(dict(version=1, target=copy.deepcopy(EXPECTED_TARGET),
                                 before=before, after=engine.expected_after(before)))
    identifiers = source, bundle.digest(source_data), bundle.digest(manifest), engine.digest(before)
    data = assets.build(source_data, manifest, *identifiers)
    return source_data, manifest, identifiers, data


class AssetsTests(unittest.TestCase):
    def setUp(self):
        self.source_data, self.manifest, self.ids, self.data = fixture()
        self.sha = bundle.digest(self.data)

    def test_private_restore_exact_bytes_no_execution_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = assets.restore(self.data, self.sha, *self.ids, Path(directory))
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(sorted(p.name for p in root.iterdir()), sorted(assets.FILES))
            for name, data in zip(assets.FILES, (self.source_data, self.manifest)):
                self.assertEqual((root / name).read_bytes(), data)
                self.assertEqual((root / name).stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                assets.restore(self.data, self.sha, *self.ids, Path(directory))

    def test_each_independent_identifier_and_corrupt_bytes_refused(self):
        for i in range(4):
            ids = list(self.ids)
            ids[i] = 'b' * len(ids[i])
            with self.subTest(i=i), self.assertRaises(Exception):
                assets.decode(self.data, self.sha, *ids)
        for data in (self.data + b'\n', b'', b'[' * 2000, b'x' * (assets.MAX_BYTES + 1)):
            with self.assertRaises(Exception):
                assets.decode(data, bundle.digest(data), *self.ids)
        with self.assertRaises(Exception):
            assets.decode(self.data, '0' * 64, *self.ids)

    def test_duplicate_noncanonical_extra_and_base64_fields(self):
        value = json.loads(self.data)
        cases = [b'{"binding":null,' + self.data[1:], json.dumps(value).encode()]
        for key, replacement in [('source', value['source'] + '\n'), ('manifest', 7),
                                 ('extra', True), ('binding', {})]:
            bad = copy.deepcopy(value)
            bad[key] = replacement
            cases.append(bundle.canonical(bad))
        for data in cases:
            with self.assertRaises(Exception):
                assets.decode(data, bundle.digest(data), *self.ids)

    def test_wrong_target_baseline_active_state_and_non_six_delta(self):
        original = json.loads(self.manifest)
        for mutation in ('target', 'baseline_target', 'enabled', 'receipts', 'queue', 'helper', 'table'):
            value = copy.deepcopy(original)
            if mutation == 'target':
                value['target']['recipient'] = 'wrong'
            elif mutation == 'baseline_target':
                value['before']['target']['neon']['branch_id'] = 'wrong'
            elif mutation == 'enabled':
                value['before']['config'][0]['enabled'] = True
            elif mutation in ('receipts', 'queue'):
                value['before']['receipts' if mutation == 'receipts' else 'nonterminal_tasks'] = 1
            elif mutation == 'helper':
                value['after']['functions'][-1]['acl'] = [dict(grantee=43)]
            else:
                value['after']['tables'] = ['unexpected change']
            raw = engine.encode(value)
            with self.subTest(mutation=mutation), self.assertRaises(Exception):
                assets.build(self.source_data, raw, self.ids[0], self.ids[1], bundle.digest(raw),
                             engine.digest(value['before']))

    def test_validation_precedes_filesystem_and_partial_output_is_preserved(self):
        with patch.object(assets.os, 'mkdir') as mkdir, self.assertRaises(Exception):
            assets.restore(self.data, '0' * 64, *self.ids, Path('/unused'))
        mkdir.assert_not_called()
        actual_open = os.open
        def fail_manifest(path, *args, **kwargs):
            if path == 'manifest.json':
                raise OSError('injected')
            return actual_open(path, *args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(assets.os, 'open', side_effect=fail_manifest), self.assertRaises(OSError):
                assets.restore(self.data, self.sha, *self.ids, Path(directory))
            self.assertTrue((Path(directory) / self.sha / 'source.json').exists())
            with self.assertRaises(FileExistsError):
                assets.restore(self.data, self.sha, *self.ids, Path(directory))

    def test_public_or_symlink_parent_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            target.mkdir(mode=0o755)
            with self.assertRaises(Exception):
                assets.restore(self.data, self.sha, *self.ids, target)
            target.chmod(0o700)
            link = root / 'link'
            link.symlink_to(target)
            with self.assertRaises(Exception):
                assets.restore(self.data, self.sha, *self.ids, link)


class OCIAssetsTests(unittest.TestCase):
    def setUp(self):
        self.source_data, self.manifest, self.ids, self.data = fixture()
        self.sha = bundle.digest(self.data)
        fake = NS(retry=NS(NoneRetryStrategy=lambda: 'NO_RETRY'), exceptions=NS(ServiceError=ServiceError))
        self.sdk = patch.dict(sys.modules, {'oci': fake})
        self.sdk.start()
        self.addCleanup(self.sdk.stop)
        self.client = FakeClient()
        self.guards = []
        self.store = adapter.OCIJournalStore(self.client, 'ci_namespace', lambda: self.guards.append(True))

    def test_create_only_readback_idempotence_separate_namespace(self):
        assets.retain(self.store, self.data, self.sha, *self.ids)
        self.assertEqual(self.store.read_assets(self.sha, *self.ids), self.data)
        self.assertEqual(len(self.client.objects), 1)
        self.assertTrue(next(iter(self.client.objects)).startswith(adapter.ASSETS_PREFIX))
        calls = len(self.guards)
        assets.retain(self.store, self.data, self.sha, *self.ids)
        self.assertEqual(len(self.guards), calls)
        puts = [kw for name, path, kw in self.client.calls if name == 'PUT']
        self.assertTrue(puts)
        self.assertTrue(all(kw.get('if_none_match') == '*' for kw in puts))

    def test_private_budget_and_conflict_fail_closed(self):
        self.client.public = True
        with self.assertRaises(Exception):
            assets.retain(self.store, self.data, self.sha, *self.ids)
        self.assertTrue(self.store.failed)
        self.assertFalse(self.client.objects)

    def test_lost_ack_is_not_retried_or_marked_success(self):
        original = self.client.put_object
        def lost(*args, **kwargs):
            original(*args, **kwargs)
            raise TimeoutError('private')
        with patch.object(self.client, 'put_object', side_effect=lost) as put:
            with self.assertRaises(TimeoutError):
                assets.retain(self.store, self.data, self.sha, *self.ids)
            self.assertEqual(put.call_count, 1)
        self.assertTrue(self.store.failed)
        self.assertEqual(len(self.client.objects), 1)
        with self.assertRaises(Exception):
            self.store.read_assets(self.sha, *self.ids)


class CandidateTests(unittest.TestCase):
    def test_private_response_binding_and_safe_report(self):
        _, manifest, ids, _ = fixture()
        report = dict(audit='NATIVE_OWNER_HOST_READ_ONLY_PASS', runtime_id='a' * 64,
                      snapshot_digest=ids[3], snapshot_approved=False, hold_unchanged=True,
                      native_enabled=False, receipts=0, nonterminal_tasks=0, light_native_execute=0,
                      production_mutations=False, elapsed_ms=12)
        response = dict(report=report, manifest=base64.b64encode(manifest).decode())
        self.assertEqual(runner.candidate_response(bundle.canonical(response)), (report, manifest))
        for key, value in (('snapshot_digest', 'b' * 64), ('snapshot_approved', True),
                           ('hold_unchanged', False), ('receipts', 1)):
            bad = copy.deepcopy(response)
            bad['report'][key] = value
            with self.subTest(key=key), self.assertRaises(Exception):
                runner.candidate_response(bundle.canonical(bad))

    def test_ssh_candidate_credentials_only_in_private_stdin(self):
        from test_native_maintenance_owner_attest import URI
        source_data, manifest, ids, _ = fixture()
        with patch.dict(os.environ, {'NATIVE_OWNER_DATABASE_URL': URI, 'GITHUB_RUN_ID': '1',
                                     'GITHUB_RUN_ATTEMPT': '1'}), \
                patch.object(runner.driver, 'build', return_value=b'wheels'), \
                patch.object(runner, 'guard'), patch.object(runner, 'bootstrap', return_value='trusted') as boot, \
                patch.object(runner, 'candidate_response', return_value=({}, manifest)), \
                patch.object(runner.subprocess, 'run', return_value=NS(returncode=0, stdout=b'private')) as run:
            runner.fetch_candidate(Path('/repo'), ids[0], '/key', '/hosts', '/wheels', source_data)
            self.assertNotIn('NATIVE_OWNER_DATABASE_URL', os.environ)
        self.assertTrue(boot.call_args.kwargs['candidate'])
        self.assertNotIn(URI, str(run.call_args.args))
        self.assertEqual(run.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin'})
        self.assertEqual(json.loads(run.call_args.kwargs['input'])['credential'], URI)
        self.assertTrue(run.call_args.kwargs['capture_output'])

    def test_failure_redacts_all_private_error_text(self):
        output = io.StringIO()
        with patch.object(runner, 'main', side_effect=RuntimeError('private owner credential')), \
                contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            runner.entrypoint()
        self.assertNotIn('private', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['audit'], 'NATIVE_RECOVERY_ASSETS_REFUSED')

    def test_owner_candidate_is_read_only_without_self_approval(self):
        from test_native_maintenance_owner_attest import OwnerAttestTests, URI
        connect, conn = OwnerAttestTests().fixture()
        _, manifest, _, _ = fixture()
        before = json.loads(manifest)['before']
        with patch.object(owner.engine, 'snapshot', return_value=before), \
                patch.object(owner.engine, 'privileges'), patch.object(owner.engine, 'prepare') as prepare, \
                patch.object(owner.engine, 'change') as change:
            report, raw = owner.candidate(connect, URI)
        self.assertTrue(conn.read_only)
        self.assertFalse(report['snapshot_approved'])
        self.assertEqual(raw, manifest)
        prepare.assert_not_called()
        change.assert_not_called()


if __name__ == '__main__':
    unittest.main()
