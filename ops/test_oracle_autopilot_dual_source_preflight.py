"""No-cloud identity checks for the protected dual-source preflight."""
import os
import unittest
from unittest.mock import patch

from ops import oracle_autopilot_dual_source_preflight as gate


class DualSourceTests(unittest.TestCase):
    def test_distinct_pinned_endpoints_and_credentials_not_emitted(self):
        for label in gate.SOURCES:
            _, _, host = gate.SOURCES[label]
            raw = f'postgresql://neondb_owner:secret@{host}/neondb?sslmode=verify-full&channel_binding=require'
            params = gate.pinned_parameters(raw, label)
            self.assertEqual(params['host'], host)
            self.assertEqual(params['sslmode'], 'verify-full')
            self.assertNotIn('secret', str({key: value for key, value in params.items() if key != 'password'}))

    def test_shadow_never_accepts_production_or_routing_override(self):
        host = gate.SOURCES['shadow'][2]
        valid = f'postgresql://neondb_owner:secret@{host}/neondb?sslmode=verify-full&channel_binding=require'
        for raw in (valid.replace(host, gate.SOURCES['production'][2]),
                    valid + '&host=other.example',
                    valid + '&sslmode=disable',
                    valid.replace('/neondb?', '/autopilot?')):
            with self.assertRaises(ValueError):
                gate.pinned_parameters(raw, 'shadow')

    def test_branch_and_schema_generation_must_match(self):
        class Cursor:
            def __init__(self, row):
                self.row = row
            def execute(self, query):
                pass
            def fetchone(self):
                return self.row
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        class Connection:
            def __init__(self, row):
                self.row = row
            def cursor(self):
                return Cursor(self.row)
            def rollback(self):
                pass
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        class Psycopg:
            row = None
            @classmethod
            def connect(cls, **kwargs):
                return Connection(cls.row)
        import sys
        good = (gate.PROJECT, gate.SOURCES['shadow'][1], 'neondb', 180006, 14, 26, 0, 100)
        with patch.dict(sys.modules, {'psycopg': Psycopg}):
            Psycopg.row = good
            self.assertEqual(gate.inspect('shadow', {})['relations'], 14)
            for row in ((good[0], gate.SOURCES['production'][1], *good[2:]),
                        (*good[:6], 4, good[7])):
                Psycopg.row = row
                with self.assertRaisesRegex(ValueError, 'SOURCE_BRANCH_OR_GENERATION_MISMATCH'):
                    gate.inspect('shadow', {})

    def test_no_untrusted_invocation(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'fork/project',
                                     'GITHUB_REF': 'refs/heads/main'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'TRUSTED_MAIN_REQUIRED'):
                gate.main()


if __name__ == '__main__':
    unittest.main()
