"""Public-seam regressions for observation, prohibition and action identity."""
from importlib import import_module
from itertools import permutations

import pytest


RUNTIMES = [import_module('bridge_school_api.' + name) for name in (
    'l1_canonical_runtime', 'l1_canonical_runtime_v2',
    'l1_canonical_runtime_v3', 'l1_canonical_runtime_v4',
)]
API_IDS = ['v1', 'v2', 'v3', 'v4']
RuleEvaluation = RUNTIMES[0].RuleEvaluation
STOPPER_RULE = 'RULE-L1-NT-STOPPER-REQUIRED-COMPETITION'


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('level,action', [(6, 'SMALL_SLAM'), (7, 'GRAND_SLAM')])
@pytest.mark.parametrize('unused_score', [{}, {'contract_trick_score': None},
                                          {'contract_trick_score': False},
                                          {'contract_trick_score': 'invalid'}])
def test_review_slam_classification_requires_only_level(api, level, action, unused_score):
    result = api.evaluate('RULE-L1-SCORING-GAME-SLAM-CLASS', {'level': level, **unused_score})
    assert (result.status, result.action) == ('MATCH', action)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('level', [1, 5])
def test_review_non_slam_classification_still_requires_score(api, level):
    result = api.evaluate('RULE-L1-SCORING-GAME-SLAM-CLASS', {'level': level})
    assert (result.status, result.action) == ('NO_MATCH', None)


@pytest.mark.parametrize('api', RUNTIMES[1:], ids=API_IDS[1:])
@pytest.mark.parametrize('context', [None, [], 'invalid', 42])
def test_review_unknown_registry_rule_blocks_before_context_validation(api, context):
    result = api.evaluate('SYNTHETIC_UNKNOWN', context)
    assert (result.status, result.action) == ('BLOCK', 'UNKNOWN_RULE_ID')


@pytest.mark.parametrize('api', RUNTIMES[1:], ids=API_IDS[1:])
def test_review_unknown_registry_rule_keeps_profile_isolation_first(api):
    result = api.evaluate('SYNTHETIC_UNKNOWN', None, system_version='SCHOOL_TOURNAMENT_CURRENT_V1')
    assert (result.status, result.action) == ('NO_MATCH', None)


def resolve(api, values):
    return (api.resolve if api is RUNTIMES[0] else api.resolve_registered)(values)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('rule,context', [
    ('RULE-L1-PLAY-NT-COUNT-TOP-TRICKS', {}),
    ('RULE-L1-PLAY-NT-FIND-EXTRA-TRICKS', {'top_tricks': 0}),
    ('RULE-L1-WEAK2-RESPONSE-BANDS', {}),
    ('RULE-L1-HCP-COUNT', {'A': 0, 'K': 0, 'Q': 0}),
    ('RULE-L1-1NT-STAYMAN', {'HCP': 8, 'has_4_card_major': True}),
    ('RULE-L1-WEAK2-OPEN', {'HCP': 9, 'called_suit_length': 6}),
    ('RULE-L1-SCORING-VUL-BONUS-UNDERTRICKS', {'class': 'GAME'}),
])
def test_missing_observation_cannot_supply_a_default(api, rule, context):
    result = api.evaluate(rule, context)
    assert (result.status, result.action) == ('NO_MATCH', None)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('value', [None, False, True, '0', 0.0, -1, 14])
def test_count_requires_an_in_range_integer_observation(api, value):
    result = api.evaluate('RULE-L1-PLAY-NT-COUNT-TOP-TRICKS', {'top_tricks': value})
    assert (result.status, result.action) == ('NO_MATCH', None)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
def test_explicit_zero_false_and_unrelated_fields_remain_valid(api):
    zero = api.evaluate('RULE-L1-PLAY-NT-COUNT-TOP-TRICKS', {'top_tricks': 0})
    assert zero.status == 'MATCH' and type(zero.action) is int and zero.action == 0
    bonus = api.evaluate('RULE-L1-SCORING-VUL-BONUS-UNDERTRICKS', {'class': 'GAME', 'vulnerable': False})
    assert (bonus.status, bonus.action) == ('MATCH', 300)
    tricks = api.evaluate('RULE-L1-CONTRACT-REQUIRED-TRICKS', {'contract_level': 3, 'HCP': 'unrelated'})
    assert (tricks.status, tricks.action) == ('MATCH', 9)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
def test_profile_isolation_precedes_context_conversion(api):
    result = api.evaluate('RULE-L1-HCP-COUNT', None, system_version='SCHOOL_TOURNAMENT_CURRENT_V1')
    assert (result.status, result.action) == ('NO_MATCH', None)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('ten', ['ST', 'S10', '\u2660T', '\u266010'])
def test_ten_aliases_win_the_same_supplied_trick(api, ten):
    result = api.evaluate('RULE-L1-TRICK-WINNER', {'cards': [ten, 'S9', 'S8', 'S7'], 'trump': 'NT'})
    assert (result.status, result.action) == ('MATCH', ten)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('context', [
    {'cards': ['ST', 'S10', 'S9', 'S8'], 'trump': 'NT'},
    {'cards': ['\u2660T', 'S10', 'S9', 'S8'], 'trump': 'NT'},
    {'cards': ['S1A', 'S9', 'S8', 'S7'], 'trump': 'NT'},
    {'cards': ['S010', 'S9', 'S8', 'S7'], 'trump': 'NT'},
    {'cards': ['SA', 'H2', 'S9', 'S8'], 'lead': 'H', 'trump': 'NT'},
    {'cards': ['SA', 'S9', 'S8', 'S7']},
    {'cards': ['SA', 'S9', 'S8', 'S7'], 'trump': False},
])
def test_invalid_or_duplicate_physical_trick_has_no_winner(api, context):
    result = api.evaluate('RULE-L1-TRICK-WINNER', context)
    assert (result.status, result.action) == ('NO_MATCH', None)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('unknown', [False, True])
def test_applicable_nt_constraint_filters_before_ranking(api, unknown):
    context = {'considering_NT': True, 'opponent_suit_known': True}
    if not unknown:
        context['stopper_in_opponent_suit'] = False
    block = RUNTIMES[3].evaluate(STOPPER_RULE, context)
    nt = RUNTIMES[3].evaluate('RULE-L1-MINOR-NT-RESPONSE-BANDS',
        {'HCP': 13, 'no_preferred_own_suit': True, 'no_minor_fit': True})
    suit = api.evaluate('RULE-L1-OPEN-1H', {'HCP': 13, 'H': 5, 'S': 4})
    assert block.status == 'BLOCK' and nt.action == '3NT'
    for items in permutations([block, nt]):
        assert resolve(api, items).status == 'BLOCK'
    for items in permutations([block, nt, suit]):
        result = resolve(api, items)
        assert (result.status, result.action) == ('MATCH', '1H')


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('action', ['1NT', '7NT', 'NT_NO_JUMP', 'LESSON2_NT_BRANCH', 'INVITE;NT_CHOICE_BRANCH'])
def test_nt_directives_cannot_escape_the_constraint(api, action):
    block = RUNTIMES[3].evaluate(STOPPER_RULE,
        {'considering_NT': True, 'opponent_suit_known': True, 'stopper_in_opponent_suit': False})
    proposal = RuleEvaluation('fixture-proposal', 'MATCH', action, 999, 99, 99)
    result = resolve(api, [proposal, block])
    assert result.status == 'BLOCK'


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
def test_reference_blocks_and_nt_substrings_do_not_veto_suit_or_procedures(api):
    reference = RUNTIMES[3].evaluate('RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE', {})
    block = RUNTIMES[3].evaluate(STOPPER_RULE,
        {'considering_NT': True, 'opponent_suit_known': True, 'stopper_in_opponent_suit': False})
    for action in ['2H', 'CONTINUE', 'COUNT', 'GAME;MAJOR_BRANCH']:
        allowed = RuleEvaluation('fixture-allowed', 'MATCH', action)
        assert resolve(api, [reference, block, allowed]) is allowed


@pytest.mark.parametrize('api', RUNTIMES[2:], ids=API_IDS[2:])
def test_explicit_false_nt_applicability_does_not_require_stopper(api):
    result = api.evaluate(STOPPER_RULE, {'considering_NT': False})
    assert (result.status, result.action) == ('NO_MATCH', None)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
def test_action_dict_key_order_does_not_create_a_canon_conflict(api):
    left = {'bid': '2H', 'context': {'level': 2, 'forcing': False}, 'trace': ['A', ('B', 0)]}
    right = {'trace': ['A', ('B', 0)], 'context': {'forcing': False, 'level': 2}, 'bid': '2H'}
    items = [RuleEvaluation('fixture-a', 'MATCH', left), RuleEvaluation('fixture-b', 'MATCH', right)]
    result = resolve(api, items)
    assert result.status == 'MATCH' and result.action == left


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
@pytest.mark.parametrize('left,right', [
    (True, 1), (1, 1.0), ([1], (1,)), ({'v': True}, {'v': 1}),
    ({True: 'x'}, {1: 'x'}), ({'v': ['2H']}, {'v': ['2S']}),
])
def test_distinct_action_types_or_values_still_conflict(api, left, right):
    result = resolve(api, [RuleEvaluation('fixture-a', 'MATCH', left), RuleEvaluation('fixture-b', 'MATCH', right)])
    assert (result.status, result.action) == ('CANON_CONFLICT', None)


@pytest.mark.parametrize('api', RUNTIMES, ids=API_IDS)
def test_explicit_conflict_still_prevents_any_action(api):
    conflict = RuleEvaluation('fixture-conflict', 'CANON_CONFLICT', reason='unresolved source conflict')
    match = RuleEvaluation('fixture-suit', 'MATCH', '2H', 999, 99, 99)
    result = resolve(api, [match, conflict])
    assert (result.status, result.action) == ('CANON_CONFLICT', None)
