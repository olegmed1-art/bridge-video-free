"""Protocol/parity regressions: null diagnostic identity is never a card."""
import ast
import copy
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'bridge_vision'
namespace = types.ModuleType('bridge_vision')
namespace.__path__ = [str(PACKAGE)]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault('bridge_vision', namespace)
helper = load('bridge_vision.factor_identity_diagnostic', PACKAGE / 'factor_identity_diagnostic.py')
compat = load('bridge_vision.factor_identity_compat', PACKAGE / 'factor_identity_compat.py')
EXPERIMENT = ROOT / 'experiments/recognizer_factor_tie'
forks = load('factor_consumer_forks', EXPERIMENT / 'consumer_forks.py')


def forked_module(name, source):
    module = types.ModuleType(name)
    exec(compile(source, name, 'exec'), module.__dict__)
    return module


def fixture():
    ranks = [.1] * 13
    ranks[helper.RANKS.index('5')] = .97
    ranks[helper.RANKS.index('6')] = .90
    suits = [.1, .1, .84, .1]
    gates = {k: True for k in ('score', 'rank_margin', 'suit_margin', 'whole_corner_score',
                             'white_retention', 'whole_corner_top1_agreement')}
    gates['identity_margin'] = False
    old = dict(version='legacy-v1', best_card='6H', joint_score=.84, identity_margin=0.,
               rank_margin=.07, suit_margin=.74, whole_corner_score=.92,
               white_retention=.95, gates=gates, rank_roi_local=[4,4,25,26],
               suit_roi_local=[3,27,20,45], roi_xyxy=[10,20,43,67], roi_bgr_sha256='a'*64)
    return old, ranks, suits


class FactorCompatibilityTests(unittest.TestCase):
    def test_declared_json_schema_with_independent_validator(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            if os.environ.get('FACTOR_SCHEMA_VALIDATION_REQUIRED') == '1':
                self.fail('required schema validator is missing')
            self.skipTest('schema engine unavailable locally; required in CI')
        schema = json.loads((EXPERIMENT / 'FACTOR_DIAGNOSTIC_SCHEMA.json').read_bytes())
        Draft202012Validator.check_schema(schema)
        validate = Draft202012Validator(schema).validate
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        validate(old)
        validate(current)
        for change in (dict(best_card='5H'), dict(automatic_acceptance_allowed=0),
                       dict(card_for_acceptance='5H'), dict(identity_margin=False),
                       dict(tie_policy_version='future-v9')):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    # ValidationError is not ValueError; convert only here so
                    # the test also explicitly checks that validation rejects.
                    errors = list(Draft202012Validator(schema).iter_errors(dict(current, **change)))
                    if errors:
                        raise ValueError('schema rejected')

    def test_legacy_conflict_gets_current_view_and_keeps_legacy_parity(self):
        old, ranks, suits = fixture()
        before = copy.deepcopy(old)
        view = compat.factor_parity_views(old, old, ranks, suits)
        self.assertIs(view['stored_legacy'], old)
        self.assertTrue(view['check'])
        self.assertEqual(view['current_recomputed']['identity_status'], 'TIE')
        self.assertIsNone(view['current_recomputed']['best_card'])
        self.assertEqual(old, before)

    def test_current_tie_uses_authenticated_legacy_view(self):
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        compat.validate_tie_diagnostic(current)
        view = compat.factor_parity_views(current, old, ranks, suits)
        self.assertIs(view['stored_legacy'], old)
        self.assertTrue(view['check'])
        self.assertEqual(view['current_recomputed'], current)

    def test_untouched_diagnostic_has_no_optional_export_fields(self):
        old, ranks, suits = fixture()
        old['best_card'] = '5H'
        view = compat.factor_parity_views(old, old, ranks, suits)
        self.assertIs(view['stored_legacy'], old)
        self.assertIsNone(view['current_recomputed'])
        self.assertIsNone(view['check'])

    def test_tampered_unknown_or_future_tie_fails_parity(self):
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        changes = [dict(best_card='5H'), dict(status='ACCEPTED'),
                   dict(card_for_acceptance='5H'), dict(automatic_acceptance_allowed=True),
                   dict(component_candidate='AH'), dict(tie_policy_version='future-v9'),
                   dict(extra_authority=True), dict(legacy_factor_diagnostic={})]
        for change in changes:
            with self.subTest(change=change):
                bad = dict(current, **change)
                view = compat.factor_parity_views(bad, old, ranks, suits)
                self.assertFalse(view['check'])
                self.assertIs(view['stored_legacy'], bad)
                self.assertIsNone(view['current_recomputed'])

    def test_other_roi_or_changed_recomputed_identity_fails(self):
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        for change in (dict(roi_bgr_sha256='b'*64), dict(roi_xyxy=[11,20,44,67]),
                       dict(best_card='AH'), dict(identity_margin=.01)):
            with self.subTest(change=change):
                view = compat.factor_parity_views(current, dict(old, **change), ranks, suits)
                self.assertFalse(view['check'])

    def test_non_object_diagnostic_is_failed_parity(self):
        old, ranks, suits = fixture()
        for bad in (None, [], ['best_card'], 'bad', 5):
            with self.subTest(value=bad):
                self.assertFalse(compat.factor_parity_views(bad, old, ranks, suits)['check'])

    def test_changed_scalar_or_gate_is_not_hidden_by_legacy_unwrap(self):
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        bad = copy.deepcopy(current)
        bad['rank_margin'] = .99
        self.assertFalse(compat.factor_parity_views(bad, old, ranks, suits)['check'])
        bad = copy.deepcopy(current)
        bad['legacy_factor_diagnostic']['gates']['score'] = False
        self.assertFalse(compat.factor_parity_views(bad, old, ranks, suits)['check'])

    def test_json_roundtrip_and_protocol_negative_types(self):
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        restored = json.loads(json.dumps(current, allow_nan=False))
        self.assertEqual(compat.validate_tie_diagnostic(restored), 'TIE')
        for key, value in [('automatic_acceptance_allowed', 0), ('identity_margin', False),
                           ('best_card', 'None'), ('legacy_factor_diagnostic', None)]:
            with self.subTest(key=key):
                bad = dict(restored, **{key:value})
                with self.assertRaises(ValueError):
                    compat.validate_tie_diagnostic(bad)


class ConsumerForkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = (EXPERIMENT / 'compatibility-fixtures/component_margin_candidate.py.txt').read_bytes()
        cls.margin_raw = forks.fork_margin(raw)
        cls.margin = forked_module('prepared_margin', cls.margin_raw)

    def test_explicit_null_tie_refuses_before_legacy_identity_validation(self):
        old, ranks, suits = fixture()
        current = helper.mark_factor_identity_tie(old, ranks, suits)
        u = dict(kind='HAND_CORNER', reason='CARD_UNCERTAIN',
                 receipt=dict(best_card='6H', best_score=.95), factor_diagnostic=current)
        d = self.margin.decide(u, dict(legacy_parity=dict(status='PASS'), observation_ref={}))
        self.assertEqual(d['status'], 'UNKNOWN')
        self.assertEqual(d['reason'], 'FACTOR_IDENTITY_TIE')
        self.assertIsNone(d['card'])
        self.assertEqual(d['original_unknown'], u)

    def test_null_or_tie_cannot_enter_positive_overlay(self):
        old, ranks, suits = fixture()
        for card, factor in [(None, old), ('INVALID', old),
                             ('5H', helper.mark_factor_identity_tie(old, ranks, suits))]:
            with self.subTest(card=card):
                u = dict(kind='HAND_CORNER', factor_diagnostic=factor)
                rows = [dict(receipt=dict(frame_sha256='a'*64), payload=dict(cards=[], unknown=[u]))]
                d = dict(diagnostic_ref=dict(frame_sha256='a'*64, unknown_index=0),
                         original_unknown=u, status='ADDITIONAL_MACHINE_CANDIDATE', card=card)
                with self.assertRaises(self.margin.CandidateError):
                    self.margin.build_overlay(rows, [d], expected_baseline_cards=0)

    def test_fork_rejects_incompatible_source_sha(self):
        raw = (EXPERIMENT / 'compatibility-fixtures/component_margin_candidate.py.txt').read_bytes()
        with self.assertRaises(ValueError):
            forks.fork_margin(raw + b'\n')


    def test_all_prepared_patches_have_no_trailing_whitespace(self):
        for p in EXPERIMENT.glob('*.patch'):
            for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
                with self.subTest(file=p.name, line=i):
                    self.assertEqual(line, line.rstrip(' \t'))

    def test_consumer_patches_apply_exactly_and_reverse(self):
        expected = {
            'release_scoring.py': forks.fork_scorer,
            'component_margin_candidate.py': forks.fork_margin,
        }
        for name, transform in expected.items():
            with self.subTest(source=name):
                raw = (EXPERIMENT / 'compatibility-fixtures' / (name + '.txt')).read_bytes()
                patch = EXPERIMENT / (name + '.patch')
                header = patch.read_text(encoding='utf-8').splitlines()[0]
                self.assertTrue(header.startswith('--- a/'))
                target = header[len('--- a/'):]
                self.assertNotIn('..', Path(target).parts)
                scratch = Path(tempfile.mkdtemp(prefix='.factor-consumer-patch-', dir=ROOT))
                staged = scratch / target
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(raw)
                def git(*args):
                    run = subprocess.run(['git', *args, str(patch)], cwd=scratch, capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                git('apply', '--check', '--whitespace=error')
                git('-c', 'core.autocrlf=false', 'apply', '--whitespace=error')
                self.assertEqual(staged.read_bytes(), transform(raw))
                git('apply', '--reverse', '--check', '--whitespace=error')
                git('-c', 'core.autocrlf=false', 'apply', '--reverse', '--whitespace=error')
                self.assertEqual(staged.read_bytes(), raw)


    def test_malformed_tie_produces_failed_parity_in_actual_scorer_tail(self):
        raw = (EXPERIMENT / 'compatibility-fixtures/release_scoring.py.txt').read_bytes()
        tree = ast.parse(forks.fork_scorer(raw))
        f = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='score_unknown')
        start = next(i for i,n in enumerate(f.body) if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id=='checks' for t in n.targets))
        f.body=f.body[start:]
        code=compile(ast.fix_missing_locations(ast.Module(body=[f],type_ignores=[])),'scorer-tail','exec')
        class A(list):
            def tolist(self): return list(self)
        old,ranks,suits=fixture()
        for missing in ('joint_score','rank_margin','gates','roi_xyxy'):
            with self.subTest(missing=missing):
                bad=copy.deepcopy(helper.mark_factor_identity_tie(old,ranks,suits));del bad[missing]
                receipt=dict(best_card='6H',best_score=.95,margin=.01,
                             roi_xyxy=old['roi_xyxy'],roi_bgr_sha256=old['roi_bgr_sha256'])
                original=dict(receipt,reason='CARD_UNCERTAIN')
                u=dict(kind='HAND_CORNER',reason='CARD_UNCERTAIN',receipt=receipt,factor_diagnostic=bad)
                scope=dict(TOLERANCE=1e-6,original_rebuilt=original,rebuilt=old,old=receipt,
                           at=dict(rank_scores=ranks,suit_scores=suits),best=A(),whole_best=A(),
                           rank_max=A(),suit_max=A(),locations=[],whole_locations=[],regs=[])
                exec(code,scope)
                result=scope['score_unknown'](None,u,None,None,None,None,{})
                self.assertEqual(result['legacy_parity']['status'],'FAIL')
                self.assertIs(result['legacy_parity']['checks']['factor_tie_protocol'],False)


if __name__ == '__main__':
    unittest.main()
