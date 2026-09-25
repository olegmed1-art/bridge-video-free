import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
from ops import oracle_light_node_mode_repair as repair


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.base = Path(self.tmp.name)
        self.path = self.base / 'version'
        self.path.mkdir(mode=0o775)
        self.path.chmod(0o775)
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_repair(self, pre=lambda:None, post=lambda:None, mode=0o775):
        repair.transaction(self.path, os.getuid(), os.getgid(), mode,
                           pre, post, self.events.append)

    def mode(self, path=None):
        return stat.S_IMODE((path or self.path).stat().st_mode)

    def test_only_directory_group_write_changes(self):
        child = self.path / 'child'
        child.write_text('untouched')
        child.chmod(0o664)
        self.run_repair()
        self.assertEqual(self.mode(), 0o755)
        self.assertEqual(self.mode(child), 0o664)
        self.assertEqual(self.events[-1]['action'], 'APPLIED_VERIFIED')

    def test_preflight_failure_never_chmods(self):
        with self.assertRaises(RuntimeError):
            self.run_repair(pre=lambda:(_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(self.mode(), 0o775)

    def test_postflight_failure_restores_original_mode(self):
        with self.assertRaises(RuntimeError):
            self.run_repair(post=lambda:(_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(self.mode(), 0o775)
        self.assertEqual(self.events[-1]['rollback'], 'ROLLBACK_DONE')

    def test_renamed_directory_rolls_back_held_inode(self):
        moved = self.base / 'moved'
        def post():
            self.path.rename(moved)
            raise RuntimeError()
        with self.assertRaises(RuntimeError):
            self.run_repair(post=post)
        self.assertEqual(self.mode(moved), 0o775)
        self.assertEqual(self.events[-1]['rollback'], 'ROLLBACK_DONE')

    def test_replacement_is_never_chmodded(self):
        moved = self.base / 'moved'
        def post():
            self.path.rename(moved)
            self.path.mkdir(mode=0o700)
        with self.assertRaises(RuntimeError):
            self.run_repair(post=post)
        self.assertEqual(self.mode(moved), 0o775)
        self.assertEqual(self.mode(), 0o700)

    def test_symlink_in_middle_rejected(self):
        link = self.base / 'alias'
        link.symlink_to(self.path, target_is_directory=True)
        leaf = self.path / 'leaf'
        leaf.mkdir(mode=0o775)
        with self.assertRaises(OSError):
            repair.DirectoryChain(link/'leaf', os.getuid(), os.getgid())

    def test_parent_swap_during_open_never_touches_decoy(self):
        parent = self.base / 'parent'
        parent.mkdir()
        original = parent/'version'
        original.mkdir()
        original.chmod(0o775)
        moved = self.base/'saved'
        decoy = self.base/'decoy'
        decoy.mkdir()
        (decoy/'version').mkdir(mode=0o700)
        real_open = os.open
        def swapping_open(name, flags, **kwargs):
            if name == 'version' and kwargs.get('dir_fd') is not None:
                parent.rename(moved)
                parent.symlink_to(decoy, target_is_directory=True)
            return real_open(name, flags, **kwargs)
        with patch.object(repair.os, 'open', side_effect=swapping_open):
            with self.assertRaises(RuntimeError):
                repair.DirectoryChain(original, os.getuid(), os.getgid())
        self.assertEqual(self.mode(decoy/'version'), 0o700)
        self.assertEqual(self.mode(moved/'version'), 0o775)

    def test_unexpected_initial_mode_blocks(self):
        with self.assertRaises(RuntimeError):
            self.run_repair(mode=0o770)
        self.assertEqual(self.mode(), 0o775)

    def test_prewrite_drift_blocks(self):
        def emit(event):
            self.events.append(event)
            if event.get('action') == 'PREPARED':
                self.path.chmod(0o770)
        with self.assertRaises(RuntimeError):
            repair.transaction(self.path, os.getuid(), os.getgid(), 0o775,
                               lambda:None, lambda:None, emit)
        self.assertEqual(self.mode(), 0o770)

    def test_rollback_does_not_overwrite_unknown_mode(self):
        def post():
            self.path.chmod(0o700)
            raise RuntimeError()
        with self.assertRaises(RuntimeError):
            self.run_repair(post=post)
        self.assertEqual(self.mode(), 0o700)
        self.assertEqual(self.events[-1]['rollback'], 'ROLLBACK_UNCERTAIN')

    def test_acl_presence_blocks(self):
        with patch.object(repair.os, 'getxattr', return_value=b'acl'):
            with self.assertRaises(RuntimeError):
                self.run_repair()
        self.assertEqual(self.mode(), 0o775)

    def test_failed_restore_is_explicit(self):
        real = os.fchmod
        def fail_restore(fd, mode):
            if mode == 0o775:
                raise OSError('simulated')
            real(fd, mode)
        with patch.object(repair.os, 'fchmod', side_effect=fail_restore):
            with self.assertRaises(RuntimeError):
                self.run_repair(post=lambda:(_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(self.events[-1]['rollback'], 'ROLLBACK_UNCERTAIN')
        self.assertEqual(self.events[-1]['old_mode'], '0775')


if __name__ == '__main__':
    unittest.main()
