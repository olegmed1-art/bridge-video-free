"""Source-only Definition candidate to lesson preflight; no active route.

The existing accepted-composite implementation is EX-0002-specific and
test-only. Until a Definition-specific accepted lesson consumer exists this
module cannot yield an assignment, rubric, assessor, or session binding.
"""
from copy import deepcopy

from bridge_contracts.card_play_definition_admission import _digest
from bridge_contracts.video_teacher_read import read_teacher_video_candidate
from database.definition_initial_admission import DefinitionInitialAdmission, proposal_check
from database.definition_initial_postgres import DefinitionInitialPostgres


def _abstain(reason, *, source_bound=False):
    return dict(status='ABSTAIN', reason=reason, source_bound=source_bound,
                accepted_canon=False, lesson_admission_verified=False,
                assignment=None, rubric=None, assessor=None, feedback=None,
                teacher_packet=None, session_binding=None, FULL_CALL='ABSTAIN')


def _draft_lesson_matches(lesson, proposal):
    """Correlate draft assets only; self-declared hashes grant no authority."""
    try:
        source = proposal['knowledge_version']['content']['definition_evidence']['source_binding']
        owner = proposal['knowledge_version']['content']['definition_evidence']['owner_confirmation']
        binding = lesson['binding']
        assessor = lesson['assessor_binding']
        return (lesson['status'] == 'DRAFT' and
            lesson['authority']['accepted_lesson'] is False and
            lesson['session_binding'] is None and lesson['FULL_CALL'] == 'ABSTAIN' and
            type(binding['proposal_version']) is int and binding['proposal_version'] == 1 and
            type(binding['lesson_version']) is int and binding['lesson_version'] == 1 and
            type(binding['rubric_version']) is int and binding['rubric_version'] == 1 and
            type(binding['assessor_version']) is int and binding['assessor_version'] == 1 and
            binding['skill_proposal_id'] == proposal['knowledge_item']['stable_key'] and
            binding['source_candidate_id'] == source['source_candidate_id'] and
            binding['source_text_sha256'] == source['raw_text_sha256'] and
            binding['owner_message_ref'] == owner['message_ref'] and
            binding['owner_confirmation_sha256'] == owner['record_sha256'] and
            _digest(binding['source_interval_seconds']) == _digest(source['interval_seconds']) and
            binding['knowledge_item_id'] is None and binding['knowledge_version_id'] is None and
            binding['canon_activation_id'] is None and
            binding['lesson_id'].startswith('candidate.lesson.') and
            binding['lesson_sha256'] == _digest(lesson['public_assignment']) and
            binding['rubric_sha256'] == _digest(lesson['private_rubric']) and
            binding['rubric_id'] == lesson['private_rubric']['rubric_id'] and
            type(lesson['private_rubric']['rubric_version']) is int and
            binding['rubric_version'] == lesson['private_rubric']['rubric_version'] and
            binding['assessor_id'] == assessor['assessor_id'] and
            type(assessor['version']) is int and binding['assessor_version'] == assessor['version'] and
            binding['assessor_source_sha256'] == assessor['source_sha256'])
    except (KeyError, TypeError, ValueError):
        return False


def preflight_definition_lesson(*, candidate_id, reader, proposal, lesson_proposal, scope,
                                verification_bundle_sha256, request_key,
                                admission):
    """Cross-check the actual staged row and CURRENT accepted Canon read.

    Every service dependency is server supplied. A source-only backend, a
    locally validated proposal, or a staging read can never authorize teaching.
    Do not adapt EX-0002's accepted lesson or its assessor to this skill.
    """
    try:
        frozen = deepcopy(proposal)
        proposal_check(frozen, scope)
        if not _draft_lesson_matches(lesson_proposal, frozen):
            return _abstain('DRAFT_LESSON_BINDING_MISMATCH')
        proposal_hash = _digest(frozen)
        if type(admission) is not DefinitionInitialAdmission or \
                type(admission.backend) is not DefinitionInitialPostgres:
            return _abstain('NATIVE_ACCEPTED_READ_UNBOUND')
        if admission.scope != scope:
            return _abstain('HOST_SCOPE_MISMATCH')
        staged = read_teacher_video_candidate(candidate_id, reader=reader)
        if staged.get('status') != 'READABLE_STAGING':
            return _abstain('STAGING_READ_UNAVAILABLE')
        if staged.get('candidate_id') != candidate_id or \
                staged.get('payload_sha256') != proposal_hash:
            return _abstain('STAGING_PROPOSAL_MISMATCH')
        accepted = admission.read_accepted(
            frozen, verification_bundle_sha256=verification_bundle_sha256,
            request_key=request_key)
        if accepted.get('status') != 'ACCEPTED_READBACK' or \
                accepted.get('source_only') is not False:
            return _abstain('CURRENT_CANON_READ_UNAVAILABLE', source_bound=True)
        packet = accepted.get('teacher_packet')
        if not isinstance(packet, dict) or _digest(packet.get('scope')) != _digest(scope) or \
                _digest(packet.get('content')) != _digest(frozen['knowledge_version']['content']) or \
                not accepted.get('accepted_selectors'):
            return _abstain('ACCEPTED_PACKET_MISMATCH', source_bound=True)
        # Current read_accepted intentionally sets lesson_binding=None. No
        # Definition-specific accepted lesson/rubric/assessor is implemented.
        return _abstain('DEFINITION_ACCEPTED_LESSON_UNBOUND', source_bound=True)
    except Exception:
        return _abstain('DEFINITION_PREFLIGHT_UNAVAILABLE')


__all__ = ['preflight_definition_lesson']
