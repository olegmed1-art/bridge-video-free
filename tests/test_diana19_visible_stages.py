"""Only the new stage boundary; neutral prompts, no private source publication."""
import hashlib
import json
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from bridge_school_api.diana19_staging import DianaJournal, require_staging
from bridge_school_api.diana19_visible_stages import VisibleStageService
from bridge_school_api.local_single_user_lesson import LocalSingleUserResolver

FIRST = 'diana19-visible-exercise-001'
SECOND = 'diana19-visible-exercise-002'


@pytest.fixture
def staged(tmp_path):
    entries = []
    for eid in (FIRST, SECOND):
        question = {'lesson_id': eid, 'title_ru': 'Neutral test',
                    'question_ru': 'Independent first question'}
        stages = [{'stage_id': 'initial', 'assistance': 'INDEPENDENT_RESPONSE', 'question': question}]
        if eid == FIRST:
            stages.append({'stage_id': 'followup', 'assistance': 'GUIDED_PRACTICE', 'question': {
                **question, 'question_ru': 'POST_COMMIT_FOLLOWUP_MARKER'}})
        entries.append({'exercise_id': eid, 'title_ru': 'Neutral test', 'stages': stages,
                        'explanation': {'paragraphs': ['PRIVATE_FINAL_EXPLANATION_MARKER']}})
    journal = DianaJournal.create(tmp_path/'new-stages.sqlite3', 'automated-test-owner', 'a'*64)
    return VisibleStageService(entries, journal)


def message(view, **patch):
    return {'request_id': str(uuid.uuid4()), 'question_sha256': view['question_sha256'],
            'stage_id': view['stage']['stage_id'],
            'answer': 'New automated test response, not a historical learner answer.', **patch}


def test_first_commit_gates_hint_and_second_commit_gates_explanation(staged):
    first = staged.view(FIRST)
    assert 'POST_COMMIT_FOLLOWUP_MARKER' not in json.dumps(first)
    assert 'PRIVATE_FINAL_EXPLANATION_MARKER' not in json.dumps(first)
    with pytest.raises(ValueError):
        staged.submit(FIRST, message(first, stage_id='followup'))
    second = staged.submit(FIRST, message(first))
    assert staged.journal.saved(FIRST+'/initial')['answer'] == second['saved']['answer']
    assert second['stage'] == {'stage_id': 'followup', 'complete': False, 'assistance': 'GUIDED_PRACTICE'}
    assert second['question']['question_ru'] == 'POST_COMMIT_FOLLOWUP_MARKER'
    assert second['explanation'] is None
    assert staged.view(SECOND)['saved'] is None
    complete = staged.submit(FIRST, message(second))
    assert complete['stage']['complete'] is True
    assert len(complete['saved_answers']) == 2
    assert complete['explanation']['paragraphs'] == ['PRIVATE_FINAL_EXPLANATION_MARKER']
    assert complete['automatic_assessment'] is False
    assert complete['saved']['assessment'] is None


def test_lost_reply_retry_reopen_and_source_change_fail_closed(staged):
    first = staged.view(FIRST)
    body = message(first)
    second = staged.submit(FIRST, body)  # Simulated lost response after commit.
    before = hashlib.sha256(staged.journal.path.read_bytes()).hexdigest()
    assert staged.submit(FIRST, body) == second
    reopened = VisibleStageService(list(staged.entries.values()), DianaJournal(
        staged.journal.path, staged.journal.owner_id, staged.journal.archive_sha))
    assert reopened.view(FIRST) == second
    with pytest.raises(ValueError):
        reopened.submit(FIRST, message(first, answer='Changed initial response'))
    assert hashlib.sha256(staged.journal.path.read_bytes()).hexdigest() == before
    reopened.entries[FIRST]['stages'][0]['question']['question_ru'] = 'Changed source'
    with pytest.raises(ValueError):
        reopened.view(FIRST)


def test_failed_save_does_not_advance_and_spades_have_one_stage(staged, monkeypatch):
    first = staged.view(FIRST)
    original = staged.journal.save
    def failed(*args, **kwargs):
        raise OSError('simulated persistence failure')
    monkeypatch.setattr(staged.journal, 'save', failed)
    with pytest.raises(OSError):
        staged.submit(FIRST, message(first))
    assert staged.view(FIRST) == first
    monkeypatch.setattr(staged.journal, 'save', original)
    spades = staged.view(SECOND)
    complete = staged.submit(SECOND, message(spades))
    assert complete['stage'] == {'stage_id': 'initial', 'complete': True, 'assistance': 'INDEPENDENT_RESPONSE'}
    assert len(complete['saved_answers']) == 1
    assert staged.view(FIRST) == first


def test_new_stage_service_keeps_existing_local_owner_origin_and_csrf_guard(staged, tmp_path):
    private = tmp_path/'context'
    private.mkdir()
    context = {'schema': 'local-single-user-runtime-v1', 'mode': 'LOCAL_SINGLE_USER',
        'owner': {'provider_key': 'local-single-user', 'provider_subject': staged.journal.owner_id,
                  'school_id': 'local-only', 'student_id': staged.journal.owner_id},
        'frontend_origin': 'http://127.0.0.1:8090', 'backend_origin': 'http://127.0.0.1:8091',
        'csrf_nonce': 'neutral-test-csrf-value-not-a-secret'}
    (private/'runtime.json').write_text(json.dumps(context), encoding='utf-8')
    app = FastAPI()
    app.state.school_spot_mode = 'LOCAL_SINGLE_USER'
    app.state.local_context_resolver = LocalSingleUserResolver(private)
    app.state.diana19_service = staged
    def request(extra=(), method='POST'):
        return Request({'type': 'http', 'app': app, 'method': method, 'scheme': 'http',
            'path': '/school/lessons/diana19/answer/'+FIRST, 'query_string': b'',
            'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 8091),
            'headers': [(k.encode(), v.encode()) for k, v in [('host', '127.0.0.1:8091'), *extra]]})
    valid = [('origin', context['frontend_origin']), ('x-csrf-token', context['csrf_nonce'])]
    assert require_staging(request(valid)) is staged
    for extra in ([], [('origin', 'https://other.invalid')], [*valid, ('x-learner-id', 'other')]):
        with pytest.raises(HTTPException) as denied:
            require_staging(request(extra))
        assert denied.value.status_code == 403
    assert staged.view(FIRST)['explanation'] is None
    assert staged.view(FIRST)['stage']['stage_id'] == 'initial'
