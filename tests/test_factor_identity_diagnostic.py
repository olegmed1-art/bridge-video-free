"""Offline regressions for a diagnostic-only factor identity correction."""
import copy
import importlib.util
import math
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import unittest


MODULE = Path(__file__).resolve().parents[1] / 'bridge_vision/factor_identity_diagnostic.py'
spec = importlib.util.spec_from_file_location('factor_identity_diagnostic', MODULE)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)
RANKS = 'AKQJT98765432'
SUITS = 'CDHS'


def detail(card='6H', margin=0.0):
    return {'best_card': card, 'identity_margin': margin, 'version': 'legacy-v1',
            'joint_score': .84, 'roi_xyxy': [10, 20, 43, 67],
            'roi_bgr_sha256': 'a' * 64,
            'gates': {'score': True, 'identity_margin': margin >= .08}}


def scores(rank='5', suit='H', other='6'):
    ranks = [.1] * 13
    ranks[RANKS.index(rank)] = .97
    ranks[RANKS.index(other)] = .90
    suits = [.1] * 4
    suits[SUITS.index(suit)] = .84
    return ranks, suits


class FactorIdentityDiagnosticTests(unittest.TestCase):
    def test_suit_capped_joint_tie_is_not_an_identity(self):
        original = detail()
        ranks, suits = scores()
        self.assertEqual(min(ranks[RANKS.index('5')], suits[2]),
                         min(ranks[RANKS.index('6')], suits[2]))
        frozen = copy.deepcopy(original)
        result = candidate.mark_factor_identity_tie(original, ranks, suits)
        self.assertIsNone(result['best_card'])
        self.assertIsNone(result['card_for_acceptance'])
        self.assertEqual(result['identity_status'], 'TIE')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(result['automatic_acceptance_allowed'])
        self.assertEqual(result['legacy_factor_diagnostic'], frozen)
        self.assertEqual(original, frozen)

    def test_legacy_component_agreement_keeps_same_object(self):
        ranks, suits = scores()
        original = detail('5H')
        self.assertIs(candidate.mark_factor_identity_tie(original, ranks, suits), original)

    def test_positive_joint_margin_keeps_same_object(self):
        ranks, suits = scores()
        original = detail(margin=.01)
        self.assertIs(candidate.mark_factor_identity_tie(original, ranks, suits), original)

    def test_component_ties_do_not_create_a_different_identity(self):
        ranks, suits = scores()
        ranks[RANKS.index('6')] = ranks[RANKS.index('5')]
        original = detail()
        self.assertIs(candidate.mark_factor_identity_tie(original, ranks, suits), original)
        ranks, suits = scores()
        suits[0] = suits[2]
        self.assertIs(candidate.mark_factor_identity_tie(original, ranks, suits), original)

    def test_rule_is_not_specific_to_rank_or_suit_or_roi(self):
        for rank in RANKS:
            other = RANKS[(RANKS.index(rank) + 1) % 13]
            for suit in SUITS:
                with self.subTest(rank=rank, suit=suit):
                    ranks, suits = scores(rank, suit, other)
                    original = detail(other + suit)
                    result = candidate.mark_factor_identity_tie(original, ranks, suits)
                    self.assertIsNone(result['best_card'])
                    self.assertEqual(result['component_candidate'], rank + suit)
                    self.assertEqual(result['status'], 'UNKNOWN')
                    self.assertEqual(result['roi_bgr_sha256'], original['roi_bgr_sha256'])

    def test_all_legacy_gates_true_cannot_accept_a_corrected_candidate(self):
        ranks, suits = scores()
        original = detail()
        original['gates'] = {'score': True, 'identity_margin': True}
        result = candidate.mark_factor_identity_tie(original, ranks, suits)
        self.assertFalse(result['automatic_acceptance_allowed'])
        self.assertIsNone(result['card_for_acceptance'])
        self.assertEqual(result['legacy_factor_diagnostic']['gates'], original['gates'])

    def test_repeated_delivery_is_idempotent(self):
        ranks, suits = scores()
        once = candidate.mark_factor_identity_tie(detail(), ranks, suits)
        self.assertIs(candidate.mark_factor_identity_tie(once, ranks, suits), once)

    def test_strictly_better_joint_is_not_mislabeled_as_a_tie(self):
        ranks, suits = scores()
        ranks[RANKS.index('6')] = .70
        original = detail()
        self.assertIs(candidate.mark_factor_identity_tie(original, ranks, suits), original)

    def test_malformed_component_arrays_fail_closed(self):
        ranks, suits = scores()
        for invalid in ([], ranks[:-1], ranks + [0.0], [math.nan] * 13,
                        [math.inf] * 13, [True] * 13, ['.9'] * 13):
            with self.subTest(invalid=str(invalid)[:30]):
                with self.assertRaises(ValueError):
                    candidate.mark_factor_identity_tie(detail(), invalid, suits)

    def test_invalid_margin_or_legacy_identity_fail_closed(self):
        ranks, suits = scores()
        for change in ({'identity_margin': math.nan}, {'identity_margin': -.1},
                       {'best_card': 'UNKNOWN'}, {'best_card': None}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    candidate.mark_factor_identity_tie(dict(detail(), **change), ranks, suits)


class SourcePatchPackagingTests(unittest.TestCase):
    def test_patch_text_has_no_trailing_whitespace(self):
        patch = MODULE.parents[1] / 'experiments/recognizer_factor_tie/rank-suit-tie-source.patch'
        for number, line in enumerate(patch.read_text(encoding='utf-8').splitlines(), 1):
            with self.subTest(line=number):
                self.assertEqual(line, line.rstrip(' \t'))

    def test_patch_applies_exactly_and_reverses_without_runtime_imports(self):
        root = MODULE.parents[1]
        package = root / 'experiments/recognizer_factor_tie'
        pins = json.loads((package / 'BASELINE_PINS.json').read_text(encoding='utf-8'))
        original = (package / 'frozen_rank_suit_candidate.py.txt').read_bytes()
        patch = package / 'rank-suit-tie-source.patch'
        self.assertEqual(hashlib.sha256(original).hexdigest(), pins['frozen_source_sha256'])
        self.assertEqual(hashlib.sha256(patch.read_bytes()).hexdigest(), pins['patch_sha256'])
        # Retain this bounded scratch directory for inspection on Windows.
        scratch = Path(tempfile.mkdtemp(prefix='.factor-source-patch-', dir=root))
        staged = scratch / 'bridge_vision/bridgit_rank_suit_candidate.py'
        staged.parent.mkdir()
        staged.write_bytes(original)
        def git(*args):
            run = subprocess.run(['git', *args, str(patch)], cwd=scratch,
                                 capture_output=True, text=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
        git('apply', '--check', '--whitespace=error')
        git('-c', 'core.autocrlf=false', 'apply', '--whitespace=error')
        self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), pins['fixed_source_sha256'])
        helper = staged.with_name('factor_identity_diagnostic.py')
        self.assertEqual(helper.read_bytes(), MODULE.read_bytes())
        self.assertEqual(hashlib.sha256(helper.read_bytes()).hexdigest(), pins['helper_sha256'])
        compile(staged.read_bytes(), str(staged), 'exec')
        git('apply', '--reverse', '--check', '--whitespace=error')
        git('-c', 'core.autocrlf=false', 'apply', '--reverse', '--whitespace=error')
        self.assertEqual(staged.read_bytes(), original)
        self.assertFalse(helper.exists())


if __name__ == '__main__':
    unittest.main()
