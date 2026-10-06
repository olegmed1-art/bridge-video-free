"""Synthetic collector checks; no actual HBA access."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

if sys.platform == "linux":
    import owner_collector as c


@unittest.skipUnless(sys.platform == "linux", "NOT_RUN: native Linux required")
class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hba-synthetic-", dir="/tmp")
        self.root = Path(self.temp.name)
        os.chmod(self.root, 0o700)
        (self.root / ".synthetic-hba").write_bytes(b"HBA_SYNTHETIC_ONLY_V1\n")
        self.data = b"# SECRET_SYNTHETIC_SENTINEL\ninclude fixture-private.conf\nlocal all fixture peer\n"
        (self.root / "pg_hba.conf").write_bytes(self.data)
        self.output = self.root / ".private-export"
        self.output.mkdir(mode=0o700)

    def tearDown(self):
        self.temp.cleanup()

    def test_private_exact_export_and_safe_projection(self):
        result = c.collect_synthetic(self.root, self.output)
        encoded = json.dumps(result)
        self.assertNotIn("SECRET_SYNTHETIC_SENTINEL", encoded)
        self.assertNotIn("fixture-private.conf", encoded)
        self.assertFalse(result["native_parser_verified"])
        self.assertFalse(result["loaded_rules_verified"])
        self.assertTrue(result["lexical_include_hint"])
        export = self.output / (result["private_export_id"] + ".bytes")
        self.assertEqual(export.read_bytes(), self.data)
        self.assertEqual(export.stat().st_mode & 0o777, 0o600)

    def test_unsafe_private_output_refused(self):
        os.chmod(self.output, 0o755)
        with self.assertRaises(c.Refused):
            c.collect_synthetic(self.root, self.output)

    def test_no_source_mutation(self):
        source = self.root / "pg_hba.conf"
        before = source.stat()
        c.collect_synthetic(self.root, self.output)
        after = source.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns, before.st_ctime_ns),
                         (after.st_ino, after.st_mtime_ns, after.st_ctime_ns))
        self.assertEqual(source.read_bytes(), self.data)

    def test_live_entrypoint_always_refused(self):
        with self.assertRaises(c.Refused):
            c.execute_live()

    def test_symlink_private_directory_refused(self):
        self.output.rmdir()
        self.output.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            c.collect_synthetic(self.root, self.output)

    def destination_contract(self):
        parent_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd, contract = c.bind_private_destination(self.output, None, synthetic_parent_fd=parent_fd)
            os.close(fd)
            return contract
        finally:
            os.close(parent_fd)

    def test_destination_missing_approved_binding_refused(self):
        with self.assertRaises(c.Refused):
            c.bind_private_destination(self.output, None)

    def test_destination_mount_identity_drift_refused(self):
        contract = self.destination_contract()
        contract["components"][-1]["mount_id"] += 1
        parent_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self.assertRaisesRegex(c.Refused, "identity/mount/ACL drift"):
                c.bind_private_destination(self.output, contract, synthetic_parent_fd=parent_fd)
        finally:
            os.close(parent_fd)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_destination_inode_replacement_refused(self):
        contract = self.destination_contract()
        self.output.rename(self.root / ".old-private-export")
        self.output.mkdir(mode=0o700)
        parent_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self.assertRaisesRegex(c.Refused, "identity/mount/ACL drift"):
                c.bind_private_destination(self.output, contract, synthetic_parent_fd=parent_fd)
        finally:
            os.close(parent_fd)
        self.assertEqual(list(self.output.iterdir()), [])

    def test_symlink_ancestor_refused_component_by_component(self):
        alias = self.root.with_name(self.root.name + "-alias")
        alias.symlink_to(self.root, target_is_directory=True)
        parent_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            with self.assertRaises(OSError):
                c.bind_private_destination(alias / ".private-export", None,
                                           synthetic_parent_fd=parent_fd)
        finally:
            os.close(parent_fd)
            alias.unlink()


    def test_live_entrypoint_refuses_before_any_file_open(self):
        with patch.object(c.os, "open", side_effect=AssertionError("live source accessed")):
            with self.assertRaises(c.Refused):
                c.execute_live()


if __name__ == "__main__":
    unittest.main(verbosity=2)
