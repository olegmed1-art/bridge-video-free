import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from ops.native_permission_writer_inventory import InventoryError, from_repository, inventory, workflow_record


class InventoryTests(unittest.TestCase):
    def test_nested_groups_and_credentials_are_hints_not_approval(self):
        source = '''on: [workflow_dispatch]
concurrency:
  group: outer
  cancel-in-progress: false
jobs:
  writer:
    if: false
    concurrency: inner
    environment: database-production
    env:
      A: ${{ secrets.NEON_DATABASE_URL }}
      B: ${{ secrets['ORACLE_SSH_PRIVATE_KEY'] }}
      C: ${{ secrets[inputs.credential] }}
    steps: []
  reused:
    uses: example/repo/.github/workflows/opaque.yml@main
    secrets: inherit
'''
        report = inventory({'a.yml': source}, 'a' * 40, 'b' * 40)
        row = report['workflows'][0]
        self.assertEqual(row['events'], ['workflow_dispatch'])
        self.assertEqual(row['secret_names'], ['NEON_DATABASE_URL', 'ORACLE_SSH_PRIVATE_KEY'])
        self.assertTrue(row['opaque_secret_reference'])
        self.assertEqual(row['review'], 'UNREVIEWED')
        self.assertEqual(row['concurrency']['group'], 'outer')
        self.assertEqual(row['jobs'][1]['concurrency']['group'], 'inner')
        self.assertEqual(row['jobs'][1]['if'], 'false')
        self.assertTrue(row['jobs'][0]['inherits_secrets'])
        self.assertEqual(report['maintenance_exclusion'], 'NOT_ESTABLISHED')

    def test_unknown_capabilities_are_not_cleared(self):
        r = workflow_record('x.yml', 'on: push\njobs:\n  x:\n    steps:\n      - run: ./opaque-script\n')
        self.assertEqual(r['priority_secret_names'], [])
        self.assertEqual(r['review'], 'UNREVIEWED')

    def test_ambiguous_yaml_and_mutable_refs_refused(self):
        with self.assertRaises(InventoryError):
            workflow_record('x.yml', 'jobs: {}\njobs: {}\n')
        with self.assertRaises(InventoryError):
            inventory({'x.yml': 'jobs: {}'}, 'main', 'b' * 40)
        with self.assertRaises(InventoryError):
            inventory({}, 'a' * 40, 'b' * 40)

    def test_git_objects_ignore_dirty_worktree_and_bind_dependency_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL).decode().strip()
            git('init')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            workflows = root / '.github/workflows'
            workflows.mkdir(parents=True)
            file = workflows / 'writer.yml'
            file.write_text('on: workflow_dispatch\njobs:\n  x:\n    steps: []\n')
            (root / 'called.py').write_text('pass\n')
            git('add', '.')
            git('commit', '-m', 'fixture')
            first = git('rev-parse', 'HEAD')
            before = from_repository(root, first)
            file.write_text('jobs: {}\n')
            self.assertEqual(from_repository(root, first), before)
            original_blob = git('rev-parse', first + ':.github/workflows/writer.yml')
            replacement_blob = git('hash-object', '-w', str(file))
            git('replace', original_blob, replacement_blob)
            self.assertEqual(from_repository(root, first), before)
            git('replace', '-d', original_blob)
            git('checkout', '--', '.github/workflows/writer.yml')
            (root / 'called.py').write_text('print("changed")\n')
            git('add', '.')
            git('commit', '-m', 'dependency changed')
            after = from_repository(root, git('rev-parse', 'HEAD'))
            self.assertEqual(before['workflows'], after['workflows'])
            self.assertNotEqual(before['source_tree'], after['source_tree'])
            (workflows / 'alias.yml').symlink_to('writer.yml')
            git('add', '.')
            git('commit', '-m', 'unsupported symlink')
            with self.assertRaises(InventoryError):
                from_repository(root, git('rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
