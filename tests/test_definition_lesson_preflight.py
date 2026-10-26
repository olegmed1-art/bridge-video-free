"""SOURCE_ONLY Definition preflight checks using synthetic lesson assets.

No native UUID, accepted Canon row, verifier receipt, or accepted lesson is
fabricated by these tests.
"""
from copy import deepcopy
import unittest
from unittest.mock import patch

from bridge_school_api.definition_lesson_preflight import preflight_definition_lesson
from database.definition_initial_admission import DefinitionInitialAdmission
from database.definition_initial_postgres import DefinitionInitialPostgres
from bridge_contracts.card_play_definition_admission import _digest
from definition_initial_fixtures import proposal

PROPOSAL = proposal()
_source = PROPOSAL['knowledge_version']['content']['definition_evidence']['source_binding']
_owner = PROPOSAL['knowledge_version']['content']['definition_evidence']['owner_confirmation']
_assignment = {'prompt_ru': 'SOURCE_ONLY: synthetic recognition prompt', 'response_choices': ['UNSURE']}
_rubric = {'rubric_id': 'candidate.rubric.synthetic', 'rubric_version': 1,
           'expected_response': {'choice': 'SOURCE_ONLY:synthetic'}}
_assessor = {'assessor_id': 'candidate.assessor.synthetic', 'version': 1,
             'source_sha256': _digest('SOURCE_ONLY:assessor')}
LESSON = {'status': 'DRAFT', 'public_assignment': _assignment,
          'private_rubric': _rubric, 'assessor_binding': _assessor,
          'authority': {'accepted_lesson': False}, 'session_binding': None,
          'FULL_CALL': 'ABSTAIN',
          'binding': {'proposal_version': 1, 'lesson_version': 1,
                      'rubric_version': 1, 'assessor_version': 1,
                      'skill_proposal_id': PROPOSAL['knowledge_item']['stable_key'],
                      'source_candidate_id': _source['source_candidate_id'],
                      'source_text_sha256': _source['raw_text_sha256'],
                      'owner_message_ref': _owner['message_ref'],
                      'owner_confirmation_sha256': _owner['record_sha256'],
                      'source_interval_seconds': _source['interval_seconds'],
                      'knowledge_item_id': None, 'knowledge_version_id': None,
                      'canon_activation_id': None,
                      'lesson_id': 'candidate.lesson.synthetic',
                      'lesson_sha256': _digest(_assignment),
                      'rubric_sha256': _digest(_rubric),
                      'rubric_id': _rubric['rubric_id'],
                      'assessor_id': _assessor['assessor_id'],
                      'assessor_source_sha256': _assessor['source_sha256']}}
SCOPE = dict(school_key='SOURCE_ONLY:school', system_profile='SOURCE_ONLY:profile',
             learner_level='SOURCE_ONLY:level', activation_scope='SOURCE_ONLY:scope')
SOURCE_CANDIDATE = _source['source_candidate_id']  # Synthetic producer ID, not a native UUID.


class Preflight(unittest.TestCase):
    def call(self, *, proposal=None, lesson=None, admission=None, candidate_id=SOURCE_CANDIDATE,
             scope=None, reader=None):
        return preflight_definition_lesson(candidate_id=candidate_id, reader=reader,
            proposal=PROPOSAL if proposal is None else proposal,
            lesson_proposal=LESSON if lesson is None else lesson,
            scope=SCOPE if scope is None else scope,
            verification_bundle_sha256='0' * 64, request_key='SOURCE_ONLY:unbound', admission=admission)

    def assert_private(self, result):
        self.assertEqual(result['status'], 'ABSTAIN')
        self.assertEqual(result['FULL_CALL'], 'ABSTAIN')
        self.assertIsNone(result['session_binding'])
        for key in ('assignment', 'rubric', 'assessor', 'feedback', 'teacher_packet'):
            self.assertIsNone(result[key])
        self.assertFalse(result['accepted_canon'])
        self.assertFalse(result['lesson_admission_verified'])

    def test_synthetic_draft_has_no_native_provider(self):
        result = self.call(admission=DefinitionInitialAdmission(scope=SCOPE))
        self.assertEqual(result['reason'], 'NATIVE_ACCEPTED_READ_UNBOUND')
        self.assert_private(result)

    def test_source_only_backend_cannot_become_genuine(self):
        class SourceOnly:
            mode = 'SOURCE_ONLY'
        result = self.call(admission=DefinitionInitialAdmission(backend=SourceOnly(), scope=SCOPE))
        self.assertEqual(result['reason'], 'NATIVE_ACCEPTED_READ_UNBOUND')
        self.assert_private(result)

    def test_producer_id_is_not_native_candidate_uuid(self):
        admission = DefinitionInitialAdmission(backend=DefinitionInitialPostgres(), scope=SCOPE)
        result = self.call(admission=admission)
        self.assertEqual(result['reason'], 'STAGING_READ_UNAVAILABLE')
        self.assert_private(result)

    def test_scope_and_payload_tamper_fail_before_read(self):
        admission = DefinitionInitialAdmission(backend=DefinitionInitialPostgres(), scope=SCOPE)
        bad = deepcopy(PROPOSAL)
        bad['knowledge_version']['status'] = 'approved'
        for result in (self.call(proposal=bad, admission=admission),
                       self.call(scope=dict(SCOPE, school_key=''), admission=admission)):
            self.assertEqual(result['reason'], 'DEFINITION_PREFLIGHT_UNAVAILABLE')
            self.assert_private(result)

    def test_staged_hash_substitution_never_reaches_canon_or_assessor(self):
        admission = DefinitionInitialAdmission(backend=DefinitionInitialPostgres(), scope=SCOPE)
        with patch('bridge_school_api.definition_lesson_preflight.read_teacher_video_candidate',
                   return_value=dict(status='READABLE_STAGING', candidate_id=SOURCE_CANDIDATE,
                                     payload_sha256='f' * 64)), \
             patch.object(admission, 'read_accepted', side_effect=AssertionError('unexpected')):
            result = self.call(admission=admission)
        self.assertEqual(result['reason'], 'STAGING_PROPOSAL_MISMATCH')
        self.assert_private(result)

    def test_draft_lesson_rubric_assessor_mismatch(self):
        admission = DefinitionInitialAdmission(backend=DefinitionInitialPostgres(), scope=SCOPE)
        changes = (
            ('skill_proposal_id', 'candidate.skill.foreign'),
            ('source_text_sha256', 'f' * 64),
            ('rubric_id', 'candidate.rubric.foreign'),
            ('assessor_source_sha256', 'f' * 64),
            ('owner_confirmation_sha256', 'f' * 64),
            ('source_interval_seconds', [556.2, 564.3]),
        )
        for key, value in changes:
            lesson = deepcopy(LESSON)
            lesson['binding'][key] = value
            with self.subTest(key=key):
                result = self.call(lesson=lesson, admission=admission)
                self.assertEqual(result['reason'], 'DRAFT_LESSON_BINDING_MISMATCH')
                self.assert_private(result)

    def test_draft_asset_hash_and_json_types(self):
        admission = DefinitionInitialAdmission(backend=DefinitionInitialPostgres(), scope=SCOPE)
        cases = []
        lesson = deepcopy(LESSON); lesson['public_assignment']['prompt_ru'] += ' changed'; cases.append(lesson)
        lesson = deepcopy(LESSON); lesson['private_rubric']['expected_response']['choice'] = 'UNSURE'; cases.append(lesson)
        lesson = deepcopy(LESSON); lesson['binding']['rubric_version'] = True; cases.append(lesson)
        lesson = deepcopy(LESSON); lesson['assessor_binding']['version'] = True; cases.append(lesson)
        for lesson in cases:
            with self.subTest(case=len(str(lesson))):
                result = self.call(lesson=lesson, admission=admission)
                self.assertEqual(result['reason'], 'DRAFT_LESSON_BINDING_MISMATCH')
                self.assert_private(result)


if __name__ == '__main__':
    unittest.main()
