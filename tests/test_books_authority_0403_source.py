"""Offline guards for the narrow Books authority schema migration."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
KEY = '0403_books_authority_core'


class Books0403Source(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wrapper = (ROOT / 'database/migrations' / f'{KEY}.sql').read_text()
        cls.core = (ROOT / 'database/migrations' / KEY / '001_core.sql').read_text()
        cls.assertion = (ROOT / 'database/migrations' / KEY / '002_assert.sql').read_text()
        cls.checker = (ROOT / 'database/migration_checks' / f'{KEY}.sql').read_text()
        cls.runner = (ROOT / 'database/scripts/migrate.sh').read_text()
        cls.native = (ROOT / 'database/tests/test_books_authority_0403_postgres.py').read_text()

    def test_exact_object_counts_and_no_runtime_grants(self):
        for statement, count in [('CREATE TABLE public.books_authority_', 5),
                                 ('CREATE FUNCTION public.books_authority_', 3),
                                 ('CREATE TRIGGER books_authority_', 5)]:
            self.assertEqual(self.core.count(statement), count)
        self.assertNotIn('SECURITY DEFINER', self.core)
        self.assertNotRegex(self.core, r'(?im)^\s*GRANT\s+')
        self.assertNotRegex(self.core, r'(?im)^\s*CREATE\s+ROLE\b')
        self.assertNotRegex(self.core, r'(?im)^\s*TRUNCATE\b')
        self.assertEqual(self.core.count('SECURITY INVOKER SET search_path = pg_catalog'), 3)

    def test_assertion_is_connected_to_both_paths(self):
        self.assertIn(r'\ir 0403_books_authority_core/002_assert.sql', self.wrapper)
        self.assertIn(r'\ir ../migrations/0403_books_authority_core/002_assert.sql', self.checker)
        self.assertIn('BOOKS_AUTHORITY_EXPECTED_DATABASE', self.runner)
        self.assertIn('BOOKS_AUTHORITY_EXPECTED_OWNER', self.runner)
        self.assertIn('Required Books authority postcondition missing', self.runner)
        self.assertLess(self.runner.index('verify_migration_postcondition "$key" "$checksum"'),
                        self.runner.index('Bootstrapping checksum for already-applied migration'))

    def test_acl_and_check_ownership_are_closed(self):
        self.assertIn('pg_catalog.aclexplode(a.attacl)', self.assertion)
        self.assertIn('a.attnum>0 AND NOT a.attisdropped AND x.grantee<>owner_id', self.assertion)
        self.assertIn('c.relname=item.value->>0', self.assertion)
        self.assertIn('pg_catalog.btrim(item.value->>1', self.assertion)
        self.assertNotIn('item.value#>>\'{}\'',''.join(
            line for line in self.assertion.splitlines() if 'k.conname=item.key' in line))

    def test_native_matrix_has_negative_boundaries(self):
        for marker in ('test_empty_install_and_exact_replay',
                       'test_partial_or_unknown_provenance_is_hold',
                       'test_rollback_after_first_create',
                       'test_column_acl_check_owner_and_missing_checker',
                       'test_read_grant_partial_acl_and_logical_revoke',
                       'test_bad_checksum_and_default_acl_hold',
                       'test_state_audit_and_immutable_boundaries',
                       'test_invoker_audit_failure_rolls_back_state'):
            self.assertIn(marker, self.native)
        self.assertIn('EXACT_DISPOSABLE_BOOKS0403_CI_REQUIRED', self.native)
        self.assertNotIn('neondb', self.native.lower())
        self.assertNotIn('WITH (FORCE)', self.native)

    def test_ci_hook_uses_existing_disposable_service(self):
        ci = (ROOT / '.github/workflows/database-ci.yml').read_text()
        self.assertEqual(ci.count('Qualify Books authority migration in disposable PostgreSQL 18 database'), 1)
        self.assertIn('image: postgres:18', ci)
        self.assertLess(ci.index('Apply migrations to ephemeral CI database'),
                        ci.index('Qualify Books authority migration in disposable PostgreSQL 18 database'))
        self.assertIn('timeout-minutes: 3', ci)


if __name__ == '__main__':
    unittest.main()
