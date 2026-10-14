import tempfile
import unittest
import hashlib
from pathlib import Path

from ops.autopilot_selective_ledger import (assert_restored, expected_keys,
                                            manifest, select_rows)


class Cursor:
    def __init__(self, rows):
        self.rows = rows
    def execute(self, sql):
        assert sql.startswith('SELECT migration_key, checksum, extract(epoch from applied_at)::text')
    def fetchall(self):
        return self.rows


class SelectiveLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        for number in range(300, 312):
            (self.directory / f'{number:04d}_autopilot_fixture.sql').write_text('-- reviewed\n')
        (self.directory / '0300_autopilot_oracle_shadow.sql').write_text('-- reviewed\n')
        self.sha = hashlib.sha256(b'-- reviewed\n').hexdigest()

    def test_shared_school_ledger_not_exported(self):
        rows = [('0050_school_change', 'a', '2026-01-01'),
                ('0300_autopilot_oracle_shadow', None, '2026-01-02'),
                ('0301_autopilot_fixture', self.sha, '2026-01-03'),
                ('0310_autopilot_unreviewed', 'd' * 64, '2026-01-04')]
        with self.assertRaisesRegex(ValueError, 'DRIFT'):
            select_rows(Cursor(rows), directory=self.directory)
        rows.pop()
        selected = select_rows(Cursor(rows), directory=self.directory)
        self.assertEqual([r[0] for r in selected], ['0300_autopilot_oracle_shadow', '0301_autopilot_fixture'])
        self.assertEqual(manifest(selected)['includes_other_school_migrations'], False)
        self.assertEqual(manifest(selected)['historical_null_checksum_keys'], ['0300_autopilot_oracle_shadow'])
        self.assertEqual(manifest(selected)['migration_checksum_complete'], False)
        assert_restored(Cursor(selected), selected)
        with self.assertRaisesRegex(ValueError, 'RESTORE_MISMATCH'):
            assert_restored(Cursor(rows), selected)
        with self.assertRaisesRegex(ValueError, 'SOURCE_MISMATCH'):
            select_rows(Cursor([('0300_autopilot_oracle_shadow', 'a' * 64, 'now')]),
                        directory=self.directory)

    def test_malformed_checksum_and_missing_base_fail(self):
        with self.assertRaises(ValueError):
            select_rows(Cursor([('0301_autopilot_fixture', 'a' * 64, 'now')]), directory=self.directory)
        with self.assertRaises(ValueError):
            select_rows(Cursor([('0300_autopilot_oracle_shadow', '', 'now')]), directory=self.directory)
        for unknown in ('0400_autopilot_future', 'autopilot_legacy_receipt'):
            with self.subTest(unknown=unknown), self.assertRaisesRegex(ValueError, 'UNREVIEWED_KEY_SHAPE'):
                select_rows(Cursor([('0300_autopilot_oracle_shadow', None, '1'),
                                    (unknown, None, '2')]), directory=self.directory)

    def test_real_migration_inventory(self):
        self.assertIn('0371_autopilot_provider_retry_budget', expected_keys())


if __name__ == '__main__':
    unittest.main()
