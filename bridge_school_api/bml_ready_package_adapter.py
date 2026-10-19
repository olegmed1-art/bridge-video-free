"""Fail-closed BML supplier boundary for the owner's existing reader callable.

This is a test-only contract adapter, not a ready-package builder or runtime
activation. It never reads BML, a source snapshot, a catalog, or another profile.
No BML ready-skill mapping or runtime/profile admission has been accepted yet.
"""
from typing import Any

VERSION = 'bml-ready-package-boundary-test-v1'
PROFILE = 'SCHOOL_TOURNAMENT_CURRENT_V1'
QUERY_FIELDS = frozenset({'system_profile', 'skill_ids', 'role', 'phase',
                          'trump', 'task_class', 'adapter_ref'})
SCHEMA = 'teacher-answer-v04-test-v1'
SCOPE = 'BML_FULL_CALL_BOUNDARY_TEST_ONLY'
CALLS = ('2C', '2D', '2H', '2S', '2NT', '3C', '3NT', '4D', '4H', '4NT')


class ReadyPackageUnavailable(ValueError):
    """No ready records may leave this unadmitted supplier."""


class BMLReadyPackageReader:
    """Match Callable[[dict], list[dict]]; fail before source/catalog access.

    The current owner supplies an L1 ENTRY_ORDER-only implementation of this
    callable. Sharing its query shape does not establish a BML skill contract.
    A future positive reader needs a separately accepted schema and admission;
    adding caller flags or generated IDs here cannot authorize one.
    """
    def __init__(self, *, test_only: bool = False):
        if test_only is not True:
            raise ReadyPackageUnavailable('TEST_ONLY_OPT_IN_REQUIRED')

    def read_eligible_skills(self, query: Any) -> list[dict[str, Any]]:
        if type(query) is not dict or set(query) != QUERY_FIELDS:
            raise ReadyPackageUnavailable('BML_READY_READER_QUERY_FIELDS')
        if query['system_profile'] != PROFILE:
            raise ReadyPackageUnavailable('BML_READY_READER_PROFILE_SCOPE')
        # No empty-list fallback: it would confuse unknown eligibility with a
        # successful database read that established zero applicable skills.
        # This refusal also preserves the upstream controller's withheld path.
        raise ReadyPackageUnavailable('BML_READY_PACKAGE_CONTRACT_NOT_ACCEPTED')


def answer_first_response(proposed_call: Any, *, assessment_mode: Any = 'FULL_CALL',
                          test_only: bool = False) -> dict[str, Any]:
    """Return the existing common withheld envelope, never source diagnostics.

    No observation, hidden cards, source text, fabricated records or old teacher
    result is consumed while this supplier has no admitted BML ready contract.
    There is deliberately no reader invocation, plan, recommendation or grade.
    This function is an additive boundary; it is not registered in the owner's
    ENTRY_ORDER controller, the API, UI or a production dispatcher.
    """
    result = {'schema': SCHEMA, 'controller_version': VERSION, 'scope': SCOPE,
              'test_only': True, 'production_ready': False, 'status': 'ABSTAIN',
              'reason': 'BML_READY_PACKAGE_CONTRACT_NOT_ACCEPTED',
              'missing_context': [], 'answer': None, 'decision': None,
              'proposal': None, 'binding': None, 'knowledge_versions': [], 'trace': []}
    if test_only is not True:
        result['reason'] = 'TEST_ONLY_OPT_IN_REQUIRED'
    elif type(assessment_mode) is not str or assessment_mode != 'FULL_CALL':
        result['reason'] = 'BML_FULL_CALL_BOUNDARY_ONLY'
    elif type(proposed_call) is not str or proposed_call not in CALLS:
        result['reason'] = 'BML_CALL_OUT_OF_SCOPE'
    return result
