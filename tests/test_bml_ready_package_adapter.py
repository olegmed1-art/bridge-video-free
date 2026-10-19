"""New boundary tests only; do not run the owner's 76 NOT_RUN tests."""
import ast
from copy import deepcopy
from hashlib import sha256
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'tests/fixtures/bml_ready_owner'
source = ROOT/'bridge_school_api/bml_ready_package_adapter.py'
spec = importlib.util.spec_from_file_location('_bml_ready_package_boundary', source)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class BMLReadyPackageBoundaryTests(unittest.TestCase):
    def assert_withheld(self, result):
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertFalse(result['production_ready'])
        for key in ('answer', 'decision', 'proposal', 'binding'):
            self.assertIsNone(result[key])
        self.assertEqual(result['knowledge_versions'], [])
        self.assertEqual(result['trace'], [])

    def test_all_ten_full_calls_withhold_all_disclosable_fields(self):
        for call in adapter.CALLS:
            with self.subTest(call=call):
                result = adapter.answer_first_response(call, test_only=True)
                self.assert_withheld(result)
                self.assertEqual(result['reason'], 'BML_READY_PACKAGE_CONTRACT_NOT_ACCEPTED')

    def test_query_shape_and_withheld_envelope_match_exact_owner_source(self):
        controller = FIXTURES/'teacher_v04_integration.py'
        self.assertEqual(sha256(controller.read_bytes()).hexdigest(),
                         'dc48578a3fc6b22fa7601a18ed1765707476e515e0ee7cdb83023d1c6fbb9b8b')
        tree = ast.parse(controller.read_text('utf-8'))
        withheld = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_withheld')
        returned = next(n.value for n in ast.walk(withheld) if isinstance(n, ast.Return))
        fields = {k.value for k in returned.keys}
        self.assertEqual(set(adapter.answer_first_response('2C', test_only=True)), fields)
        # Reader contract is checked as data/AST, without executing owner code.
        reader = FIXTURES/'teacher_entry_blocking_reader_path.py'
        self.assertEqual(sha256(reader.read_bytes()).hexdigest(),
                         'c66c596da73665c68af1f021f76a1417b7d0e6ea543185cea1f20b5a39c4d6d9')
        rt = ast.parse(reader.read_text('utf-8'))
        request = next(n for n in rt.body if isinstance(n, ast.FunctionDef) and n.name == '_request')
        query = next(n.value for n in ast.walk(request) if isinstance(n, ast.Return))
        self.assertEqual(adapter.QUERY_FIELDS, {k.value for k in query.keys})

    def test_reader_never_reports_empty_catalog_as_proven_eligibility(self):
        reader = adapter.BMLReadyPackageReader(test_only=True)
        query = dict(system_profile=adapter.PROFILE, skill_ids=[], role='RESPONDER',
                     phase='BIDDING', trump=None, task_class='FULL_CALL', adapter_ref=adapter.VERSION)
        before = deepcopy(query)
        with self.assertRaisesRegex(adapter.ReadyPackageUnavailable, '^BML_READY_PACKAGE_CONTRACT_NOT_ACCEPTED$'):
            reader.read_eligible_skills(query)
        self.assertEqual(query, before)

    def test_l1_profile_cannot_be_silently_reused_for_bml(self):
        reader = adapter.BMLReadyPackageReader(test_only=True)
        query = dict(system_profile='SCHOOL_L1_DB_V1', skill_ids=['SKILL-0003','SKILL-0004'],
                     role='declarer', phase='suit_timing', trump='NT',
                     task_class='SYMBOLIC_ENTRY_ORDER', adapter_ref='legacy')
        with self.assertRaisesRegex(adapter.ReadyPackageUnavailable, '^BML_READY_READER_PROFILE_SCOPE$'):
            reader.read_eligible_skills(query)

    def test_source_snapshot_cards_answers_and_self_authority_are_not_reader_fields(self):
        reader = adapter.BMLReadyPackageReader(test_only=True)
        base = dict(system_profile=adapter.PROFILE, skill_ids=[], role='RESPONDER',
                    phase='BIDDING', trump=None, task_class='FULL_CALL', adapter_ref=adapter.VERSION)
        for name in ('source_snapshot', 'hand_pbn', 'opponent_hands', 'exercise_answer',
                     'runtime_binding_verified', 'compiled_payload', 'source_authorized'):
            with self.subTest(field=name):
                with self.assertRaisesRegex(adapter.ReadyPackageUnavailable, '^BML_READY_READER_QUERY_FIELDS$'):
                    reader.read_eligible_skills(base | {name: True})
        for invalid in (None, [], 'query', base | {'system_profile':'WORLD'}, {}):
            with self.assertRaises(adapter.ReadyPackageUnavailable):
                reader.read_eligible_skills(invalid)

    def test_explicit_opt_in_is_required_and_diagnostics_do_not_become_answers(self):
        with self.assertRaisesRegex(adapter.ReadyPackageUnavailable, '^TEST_ONLY_OPT_IN_REQUIRED$'):
            adapter.BMLReadyPackageReader()
        for value in (False, None, 1, 'true'):
            r = adapter.answer_first_response('2C', test_only=value)
            self.assert_withheld(r)
            self.assertEqual(r['reason'], 'TEST_ONLY_OPT_IN_REQUIRED')
        for mode in ('SOURCE_CONDITIONS_ONLY', 'SHAPE_ONLY', [], None):
            r = adapter.answer_first_response('2C', assessment_mode=mode, test_only=True)
            self.assert_withheld(r)
            self.assertEqual(r['reason'], 'BML_FULL_CALL_BOUNDARY_ONLY')

    def test_unrelated_calls_cannot_enter_this_boundary(self):
        for call in ('3H', '3S', 'PASS', '2c', [], None):
            r = adapter.answer_first_response(call, test_only=True)
            self.assert_withheld(r)
            self.assertEqual(r['reason'], 'BML_CALL_OUT_OF_SCOPE')

    def test_repeated_refusal_returns_independent_empty_envelopes(self):
        first = adapter.answer_first_response('4NT', test_only=True)
        pristine = adapter.answer_first_response('4NT', test_only=True)
        first['trace'].append('stale result')
        first['knowledge_versions'].append('generated authority')
        self.assertEqual(adapter.answer_first_response('4NT', test_only=True), pristine)
        self.assert_withheld(pristine)

    def test_implementation_has_no_source_db_network_or_owner_execution_imports(self):
        tree = ast.parse(source.read_text('utf-8'))
        imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        self.assertEqual(imports, ['typing'])
        self.assertFalse(any(isinstance(n, ast.Import) for n in ast.walk(tree)))


if __name__ == '__main__':
    unittest.main(verbosity=2)
