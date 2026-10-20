"""Qualified Git source selection and real isolated Linux history transactions."""
import base64
import ast
import copy
import fnmatch
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('_native_history_contract',
                                            ROOT/'ops/light_native_service_history.py')
h = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = h
spec.loader.exec_module(h)


def artifact(source='a'*40, content=b'# reviewed source\n'):
    files = {name: dict(sha256=h.sha(content), base64=base64.b64encode(content).decode())
             for name in h.FILES}
    raw = h.encoded(dict(version=1, kind='LIGHT_NATIVE_CONTROL_SOURCE', source=source, files=files))
    return raw, h.sha(raw)


class PackageContracts(unittest.TestCase):
    def test_workflow_covers_complete_loaded_and_delivered_source_closure(self):
        raw = (ROOT/'.github/workflows/light-native-service-install.yml').read_bytes()
        section = raw.decode().split('    paths:\n', 1)[1].split('  workflow_dispatch:', 1)[0]
        patterns = [ast.literal_eval(line.strip()[2:]) for line in section.splitlines()
                    if line.strip().startswith('- ')]
        required = (*h.FILES, 'ops/light_native_service_install_runner.py',
                    'ops/oracle_known_hosts_from_scan.sh', 'tests/test_light_native_service_history.py',
                    'tests/test_light_native_service_install.py',
                    'docs/operations/native-dormant-install-i2-20261019.md',
                    '.github/workflows/light-native-service-install.yml')
        for path in required:
            self.assertTrue(any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns), path)
        code = ast.parse((ROOT/'ops/light_native_service_install.py').read_bytes())
        pin = next(node.value.value for node in code.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == 'WORKFLOW_SHA256' for target in node.targets))
        self.assertEqual(pin, h.sha(raw))

    def test_exact_qualified_digest_required(self):
        raw, accepted = artifact()
        self.assertEqual(h.package(raw, accepted)[0], 'a'*40)
        for digest in (None, 'f'*64, accepted.upper(), True):
            with self.subTest(digest=digest), self.assertRaises(h.Refusal):
                h.package(raw, digest)

    def test_no_credentials_or_environment_in_artifact_namespace(self):
        raw, _ = artifact()
        for name in ('credential.json', 'ops/.env', 'ops/key.pem', '../secret.py',
                     '/etc/bridge-school/environment', 'journals/live.json'):
            value = h.parse(raw)
            value['files'][name] = next(iter(value['files'].values()))
            changed = h.encoded(value)
            with self.subTest(path=name), self.assertRaises(h.Refusal):
                h.package(changed, h.sha(changed))

    def test_corruption_is_not_a_different_source_version(self):
        raw, _ = artifact()
        value = h.parse(raw)
        value['files'][h.FILES[0]]['base64'] = base64.b64encode(b'changed').decode()
        changed = h.encoded(value)
        with self.assertRaises(h.Refusal):
            h.package(changed, h.sha(changed))

    def test_noncanonical_and_duplicate_records_refused(self):
        raw, _ = artifact()
        for changed in (raw+b'\n', b'{"version":1,"version":1}', b'{"version":NaN}'):
            with self.subTest(record=changed[:48]), self.assertRaises(h.Refusal):
                h.package(changed, h.sha(changed))

    def test_git_builder_reads_only_the_explicit_commit(self):
        source = 'b'*40
        calls = []
        def git(argv, *, cwd, timeout):
            calls.append(argv)
            if argv == ['git', 'rev-parse', '--verify', source+'^{commit}']:
                return source.encode()+b'\n'
            self.assertEqual(argv[:2], ['git', 'show'])
            self.assertTrue(argv[2].startswith(source+':ops/'))
            return b'# old qualified commit, despite a newer worktree\n'
        with patch.object(h.subprocess, 'check_output', side_effect=git):
            raw = h.build(Path('fixture-repository'), source)
        selected, files = h.package(raw, h.sha(raw))
        self.assertEqual(selected, source)
        self.assertEqual(len(calls), 1+len(h.FILES))
        self.assertEqual(set(files.values()), {b'# old qualified commit, despite a newer worktree\n'})

    def test_branch_names_and_newest_selectors_refused_before_git(self):
        with patch.object(h.subprocess, 'check_output') as git:
            for source in ('main', 'HEAD', 'latest', 'a'*39, 'A'*40, None):
                with self.subTest(source=source), self.assertRaises(h.Refusal):
                    h.build(Path('.'), source)
            git.assert_not_called()


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Actual Linux root metadata required; never emulated as acceptance')
class LinuxHistoryContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='native-source-history-', dir='/root')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)/'control'
        self.scope = patch.multiple(h, ROOT=self.root, RELEASES=self.root/'releases',
                                    HISTORY=self.root/'history')
        self.scope.start()
        self.addCleanup(self.scope.stop)

    def test_archive_precedes_second_stage_and_preserves_previous_bytes(self):
        first, pin = artifact()
        result = h.stage(first, pin)
        self.assertFalse(result['software_installed'])
        self.assertFalse(result['service_changed'])
        self.assertFalse(result['end_to_end_accepted'])
        old = h.RELEASES/('a'*40)/'package.json'
        before = (h.read(old), h.signature(old.stat(follow_symlinks=False)))
        second, other = artifact('b'*40, b'# second qualified source\n')
        original = h.retain
        history_complete = []
        def traced(path, raw):
            if path == h.RELEASES/('b'*40)/'package.json':
                self.assertEqual(len(history_complete), 1)
                snapshot = h.parse(h.read(history_complete[0].parent/'manifest.json'))
                self.assertEqual(snapshot['previous_state'], 'PRESENT')
                self.assertEqual(snapshot['previous_versions'][0]['package_sha256'], pin)
                self.assertEqual(h.read(history_complete[0].parent/(('a'*40)+'.json')), first)
            original(path, raw)
            if path.name == 'complete.json':
                history_complete.append(path)
        with patch.object(h, 'retain', side_effect=traced):
            h.stage(second, other)
        self.assertEqual((h.read(old), h.signature(old.stat(follow_symlinks=False))), before)
        self.assertEqual(len(h.entries()), 2)
        h.history_gate(h.entries())

    def test_first_stage_records_absence_without_inventing_old_build(self):
        raw, pin = artifact()
        result = h.stage(raw, pin)
        snapshot = h.parse(h.read(h.HISTORY/result['history_sha256']/'manifest.json'))
        self.assertEqual(snapshot['previous_versions'], [])
        self.assertEqual(snapshot['previous_state'], 'ABSENT')
        self.assertEqual(snapshot['rollback']['previous_versions'], [])

    def test_partial_archive_blocks_another_source_without_repair(self):
        first, pin = artifact()
        h.stage(first, pin)
        original = h.retain
        def incomplete(path, raw):
            if path.name == 'complete.json':
                raise OSError('synthetic interrupted archive')
            original(path, raw)
        second, other = artifact('b'*40)
        with patch.object(h, 'retain', side_effect=incomplete), self.assertRaises(OSError):
            h.stage(second, other)
        before = sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        third, pin3 = artifact('c'*40)
        with self.assertRaises(h.Refusal):
            h.stage(third, pin3)
        self.assertEqual(before, sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))
        self.assertFalse((h.RELEASES/('b'*40)).exists())
        self.assertFalse((h.RELEASES/('c'*40)).exists())

    def test_partial_release_blocks_another_source_without_deletion(self):
        original = h.retain
        def incomplete(path, raw):
            if path.name == 'staged.json':
                raise OSError('synthetic lost staging acknowledgement')
            original(path, raw)
        raw, pin = artifact()
        with patch.object(h, 'retain', side_effect=incomplete), self.assertRaises(OSError):
            h.stage(raw, pin)
        self.assertEqual(h.read(h.RELEASES/('a'*40)/'package.json'), raw)
        other, digest = artifact('b'*40)
        with self.assertRaises(h.Refusal):
            h.stage(other, digest)
        self.assertFalse((h.RELEASES/('b'*40)).exists())
        self.assertEqual(h.read(h.RELEASES/('a'*40)/'package.json'), raw)

    def test_full_staging_is_readonly_observable_after_ack_loss(self):
        raw, pin = artifact()
        h.stage(raw, pin)
        before = copy.deepcopy(h.entries())
        with self.assertRaises(h.Refusal):
            h.stage(raw, pin)
        self.assertEqual(h.entries(), before)
        h.history_gate(before)

    def test_package_symlink_cannot_be_archived(self):
        raw, pin = artifact()
        h.stage(raw, pin)
        original = h.RELEASES/('a'*40)/'package.json'
        retained = original.with_name('retained-original.json')
        original.rename(retained)
        original.symlink_to(retained)
        other, digest = artifact('b'*40)
        with self.assertRaises((h.Refusal, OSError)):
            h.stage(other, digest)
        self.assertEqual(retained.read_bytes(), raw)
        self.assertFalse((h.RELEASES/('b'*40)).exists())

    def test_root_symlink_is_refused(self):
        outside = Path(self.temporary.name)/'outside'
        outside.mkdir(mode=0o700)
        self.root.symlink_to(outside, target_is_directory=True)
        raw, pin = artifact()
        with self.assertRaises(h.Refusal):
            h.stage(raw, pin)
        self.assertEqual(list(outside.iterdir()), [])

    def test_foreign_files_do_not_become_history(self):
        self.root.mkdir(mode=0o700)
        secret = self.root/'credential.json'
        secret.write_bytes(b'synthetic credential, never copy')
        raw, pin = artifact()
        with self.assertRaises(h.Refusal):
            h.stage(raw, pin)
        self.assertEqual(secret.read_bytes(), b'synthetic credential, never copy')
        self.assertEqual(list(h.HISTORY.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
