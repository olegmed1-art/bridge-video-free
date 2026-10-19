"""Targeted 0373 regression: real validator on recorded SQL/metadata, no DB.

Load only the stdlib validator and constants so this pure test needs neither a
PostgreSQL driver nor the production worker's optional imports.
"""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'ops/oracle_light_resume_probe.py'
NAMES = {'CLAIM_SHA', 'DB_FUNCTIONS', 'ACTIVE_VIEW_SHA'}
tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
nodes = [n for n in tree.body if
         (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in NAMES for t in n.targets))
         or (isinstance(n, ast.FunctionDef) and n.name in
             {'validate_database_contract', 'validate_resume_dsn'})]


def require(value):
    if not value: raise ValueError('PREFLIGHT_CONTRACT_MISMATCH')


scope = dict(hashlib=hashlib, require=require,
             parse_qs=parse_qs, urlsplit=urlsplit,
             FENCE='LIGHT_RUNTIME_MAILBOX_V3_COMPATIBILITY_FENCE_V1')
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), scope)
validate = scope['validate_database_contract']


class ResumeDBContractTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT/'tests/fixtures/light_resume_db_contract_0373.json').read_text())

    def check(self):
        validate(self.data['functions'], self.data['view'], self.data['trigger'], self.data['isolation'])

    def test_current_0373_contract_and_parent_binding(self):
        self.check()
        parent = ast.parse((ROOT/'ops/oracle_light_resume.py').read_text())
        pin = next(ast.literal_eval(n.value) for n in parent.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == 'CLAIM_SHA' for t in n.targets))
        self.assertEqual(pin, scope['CLAIM_SHA'])

    def test_original_447225_claim_is_rejected(self):
        self.assertEqual(hashlib.sha256(self.data['legacy_claim_definition'].encode()).hexdigest(),
                         '44722551c605e775fefde91ad82f15fd08d713ad8903f65265435ead5567c36e')
        self.data['functions'][0]['definition'] = self.data['legacy_claim_definition']
        with self.assertRaises(ValueError): self.check()

    def test_each_function_dependency_drift_is_rejected(self):
        original = deepcopy(self.data)
        for index in range(len(self.data['functions'])):
            with self.subTest(index=index):
                self.data = deepcopy(original)
                self.data['functions'][index]['definition'] += '\n-- drift'
                with self.assertRaises(ValueError): self.check()

    def test_owner_acl_search_path_or_effective_execute_drift_is_rejected(self):
        original = deepcopy(self.data)
        for index in range(len(original['functions'])):
            for field, value in [('owner','other'),('prosecdef',False),('provolatile','i'),
                                 ('proconfig',['search_path=public']),('acl',None),
                                 ('worker_execute',not original['functions'][index]['worker_execute'])]:
                with self.subTest(index=index,field=field):
                    self.data = deepcopy(original)
                    self.data['functions'][index][field] = value
                    with self.assertRaises(ValueError): self.check()

    def test_missing_or_duplicate_function_metadata_is_rejected(self):
        original = deepcopy(self.data)
        self.data['functions'].pop()
        with self.assertRaises(ValueError): self.check()
        self.data = original
        self.data['functions'][-1] = deepcopy(self.data['functions'][0])
        with self.assertRaises(ValueError): self.check()

    def test_active_view_drift_is_rejected(self):
        original = deepcopy(self.data)
        for field,value in [('definition',self.data['view']['definition']+' '),('owner','other'),
                            ('kind','r'),('acl',None),('options',['security_invoker=true'])]:
            with self.subTest(field=field):
                self.data = deepcopy(original); self.data['view'][field] = value
                with self.assertRaises(ValueError): self.check()

    def test_disabled_before_or_conditional_capacity_trigger_is_rejected(self):
        original = deepcopy(self.data)
        for field,value in [('enabled','D'),('type',7),('function','other'),('condition','true')]:
            with self.subTest(field=field):
                self.data = deepcopy(original); self.data['trigger'][field] = value
                with self.assertRaises(ValueError): self.check()

    def test_non_read_committed_runtime_is_rejected(self):
        self.data['isolation'] = 'repeatable read'
        with self.assertRaises(ValueError): self.check()

    def test_resume_dsn_allows_tls_and_client_metadata(self):
        scope['validate_resume_dsn']('postgresql://user:fake@example.neon.tech/neondb?'
            'sslmode=verify-full&channel_binding=require&sslrootcert=system&'
            'connect_timeout=10&application_name=light')

    def test_resume_dsn_rejects_masked_startup_options_and_overrides(self):
        base = 'postgresql://user:fake@example.neon.tech/neondb?sslmode=require&channel_binding=require'
        for extra in ('options=-c%20default_transaction_isolation%3Dserializable',
                      'service=other', 'host=other', 'user=other', 'dbname=other',
                      'sslmode=verify-full', 'application_name=a&application_name=b'):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                scope['validate_resume_dsn'](base + '&' + extra)


if __name__ == '__main__':
    unittest.main()
