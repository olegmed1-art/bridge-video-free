"""Pure-Python fail-closed checks for the future PG18 ACL regression."""

import re
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
import one_job_0060_default_acl_pg18 as acl


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0]


class _Catalog:
    signature = 'video_queue.sample(integer)'

    def __init__(self, rows, resolved_oid=11):
        self.rows = rows
        self.resolved_oid = resolved_oid

    def execute(self, query, params):
        if 'FROM pg_proc AS p' in query:
            assert params == (['sample'],)
            return _Cursor(self.rows)
        if 'to_regprocedure' in query:
            assert params == (self.signature,)
            return _Cursor([{'oid': self.resolved_oid}])
        raise AssertionError(query)


class ExactFunctionIdentityTest(unittest.TestCase):
    signature = {'sample': _Catalog.signature}

    def row(self, **changes):
        row = dict(proname='sample', oid=11, prokind='f', owner='postgres',
                   public_execute=False)
        row.update(changes)
        return row

    def test_exact_function_is_accepted(self):
        self.assertEqual(acl._functions(_Catalog([self.row()]), self.signature,
                                         'postgres'), {'sample': 11})

    def test_changed_signature_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'sample_signature'):
            acl._functions(_Catalog([self.row(oid=12)]), self.signature,
                           'postgres')

    def test_missing_expected_signature_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'sample_signature'):
            acl._functions(_Catalog([self.row(oid=12)], resolved_oid=None),
                           self.signature, 'postgres')

    def test_changed_prokind_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'sample_prokind'):
            acl._functions(_Catalog([self.row(prokind='p')]), self.signature,
                           'postgres')

    def test_extra_overload_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'function_inventory'):
            acl._functions(_Catalog([self.row(), self.row(oid=12)]),
                           self.signature, 'postgres')

    def test_reviewed_signatures_match_sql_declarations(self):
        root = Path(__file__).resolve().parents[1]

        def declarations(path):
            text = (root / path).read_text(encoding='utf-8')
            found = {}
            for match in re.finditer(
                    r'CREATE(?: OR REPLACE)? FUNCTION video_queue\.([a-z0-9_]+)'
                    r'\s*\((.*?)\)\s*RETURNS', text, re.I | re.S):
                name, arguments = match.groups()
                types = [part.strip().split()[1] for part in arguments.split(',')
                         if part.strip()]
                found[name] = f'video_queue.{name}({",".join(types)})'
            return found

        self.assertEqual(acl.SIXTY_SIGNATURES, declarations(
            'database/migrations/0060_fixed_subset_admission.sql'))
        proposals = declarations('database/proposals/0061_one_job_staging_admission_v1.sql')
        proposals.update(declarations(
            'database/proposals/0062_one_job_private_result_gate_v1.sql'))
        self.assertEqual(acl.PROPOSAL_SIGNATURES, proposals)


if __name__ == '__main__':
    unittest.main()
