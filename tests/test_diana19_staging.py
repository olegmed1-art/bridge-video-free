"""Focused persistence, reveal and admission checks; no source lessons published."""
import hashlib
from types import SimpleNamespace
import uuid

from fastapi import FastAPI, HTTPException
import pytest
from starlette.requests import Request

from bridge_school_api.diana19_staging import (
    DianaJournal, DianaStagingService, page, require_staging,
)
from bridge_school_api.local_single_user_lesson import (
    create_assignments, create_local_app, read_context,
)


@pytest.fixture
def setup(tmp_path):
    private = tmp_path/'private'
    create_assignments(private, 'http://127.0.0.1:8090', 'http://127.0.0.1:8091')
    context = read_context(private)
    app = create_local_app(private)
    # Neutral protocol fixture. The accepted three lessons are checked separately
    # against their exact archive in the persistent local UI verification.
    bundle = SimpleNamespace(questions={'diana19-q-001': {'lesson_id': 'diana19-q-001', 'question_ru': 'Neutral test question'}},
        explanations={'diana19-q-001': {'paragraphs': ['POST_SAVE_ONLY_MARKER']}},
        question_sha=lambda lesson_id: 'a'*64)
    journal = DianaJournal.create(private/'neutral-test.sqlite3', context['owner']['provider_subject'])
    service = DianaStagingService(bundle, journal)
    app.state.diana19_service = service
    return app, service, context


def request(app, *, method='GET', headers=()):
    return Request({'type': 'http', 'app': app, 'method': method, 'scheme': 'http', 'path': '/',
        'headers': [(k.encode(), v.encode()) for k, v in [('host', '127.0.0.1:8091'), *headers]],
        'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 8091), 'query_string': b''})


def message():
    return {'request_id': str(uuid.uuid4()), 'question_sha256': 'a'*64,
            'answer': 'A new automated protocol-test answer, not a learner history.'}


def test_explanation_only_after_durable_save_and_retry_is_idempotent(setup):
    _, service, _ = setup
    assert service.view('diana19-q-001')['explanation'] is None
    body = message()
    saved = service.submit('diana19-q-001', body)
    assert saved['saved']['answer'] == body['answer']
    assert saved['saved']['assessment'] is None and saved['automatic_assessment'] is False
    assert saved['explanation']['paragraphs'] == ['POST_SAVE_ONLY_MARKER']
    before = hashlib.sha256(service.journal.path.read_bytes()).hexdigest()
    assert service.submit('diana19-q-001', body) == saved
    reopened = DianaJournal(service.journal.path, service.journal.owner_id)
    assert reopened.saved('diana19-q-001') == saved['saved']
    assert hashlib.sha256(service.journal.path.read_bytes()).hexdigest() == before
    with pytest.raises(ValueError):
        service.submit('diana19-q-001', message())
    assert hashlib.sha256(service.journal.path.read_bytes()).hexdigest() == before


def test_invalid_answer_and_source_binding_never_reveal(setup):
    _, service, _ = setup
    for patch in [{'answer': ''}, {'answer': 'x'*4001}, {'question_sha256': 'b'*64}, {'request_id': 'not-uuid'}]:
        body = {**message(), **patch}
        with pytest.raises(ValueError):
            service.submit('diana19-q-001', body)
        assert service.view('diana19-q-001')['explanation'] is None
    service.submit('diana19-q-001', message())
    service.bundle.question_sha = lambda lesson_id: 'b'*64
    with pytest.raises(ValueError):
        service.view('diana19-q-001')


def test_local_admission_keeps_origin_csrf_and_owner_checks(setup):
    app, service, context = setup
    assert require_staging(request(app)) is service
    valid = [('origin', context['frontend_origin']), ('x-csrf-token', context['csrf_nonce'])]
    assert require_staging(request(app, method='POST', headers=valid)) is service
    for headers, method in [([], 'POST'), ([('origin', 'http://evil.invalid')], 'POST'),
                            ([('x-learner-id', 'other')], 'GET'),
                            ([('x-forwarded-for', '127.0.0.1')], 'GET')]:
        with pytest.raises(HTTPException) as denied:
            require_staging(request(app, method=method, headers=headers))
        assert denied.value.status_code == 403
    service.journal.owner_id = 'other'
    with pytest.raises(HTTPException) as denied:
        require_staging(request(app))
    assert denied.value.status_code == 403


def test_no_production_fallback_or_private_asset_route(setup, monkeypatch):
    app, service, _ = setup
    with pytest.raises(HTTPException) as denied:
        require_staging(request(FastAPI()))
    assert denied.value.status_code == 503
    for asset in ['server', 'candidate.json', '../server/lesson-index.json', 'diana19-source.zip']:
        with pytest.raises(HTTPException) as private:
            page(request(app), asset, service)
        assert private.value.status_code == 404
    monkeypatch.setenv('VERCEL_ENV', 'production')
    with pytest.raises(HTTPException) as denied:
        require_staging(request(app))
    assert denied.value.status_code == 503
