"""Offline guards; durable real HTTP/restart evidence is verify_persistent.py."""
import json

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from bridge_school_api.local_single_user_lesson import (
    ACTIVITIES, create_assignments, create_local_app, read_context,
)
from bridge_school_api.local_spot_session import load_store_class
from bridge_school_api.school_spot_lesson import require_learner_lesson_binding
from bridge_school_api.school_spot_repository import (
    FileTrustedJournalSource, SchoolLessonOwnershipRejected, VerifiedProviderIdentity,
)


def request(app, *, method='GET', activity='spot.a7c2', headers=(), client='127.0.0.1'):
    return Request({'type': 'http', 'app': app, 'method': method, 'scheme': 'http',
        'path': '/school/lessons/spot-cycle/', 'query_string': ('activity='+activity).encode(),
        'client': (client, 50000), 'server': ('127.0.0.1', 8091),
        'headers': [(k.lower().encode(), v.encode()) for k, v in
                    [('host', '127.0.0.1:8091'), *headers]]})


@pytest.fixture
def local(tmp_path):
    private = tmp_path / 'private'
    result = create_assignments(private, 'http://127.0.0.1:8090', 'http://127.0.0.1:8091')
    assert result['answer_records_created'] == 0
    return private, create_local_app(private), read_context(private)


def test_fresh_pair_and_reopen_without_recreation(local):
    private, app, _ = local
    sessions = set()
    for activity in ACTIVITIES:
        binding = require_learner_lesson_binding(request(app, activity=activity))
        sessions.add(binding.session_id)
        state = load_store_class()(binding.journal_path, binding.session_id).resume()
        assert state['revision'] == 1
        assert state['current_assignment']['prompt']['exercise_id'] == 'entry.e1'
    assert len(sessions) == 2
    with pytest.raises(ValueError):
        create_assignments(private, 'http://127.0.0.1:8090', 'http://127.0.0.1:8091')


def test_local_admission_preserves_csrf_and_owner_guards(local):
    _, app, context = local
    write = [('origin', context['frontend_origin']), ('x-csrf-token', context['csrf_nonce'])]
    assert require_learner_lesson_binding(request(app, method='POST', headers=write))
    cases = [dict(method='POST'), dict(method='POST', headers=[('origin', 'http://evil.invalid')]),
        dict(headers=[('x-learner-id', 'other')]), dict(headers=[('x-school-id', 'other')]),
        dict(headers=[('x-forwarded-for', '127.0.0.1')]), dict(client='192.0.2.1'),
        dict(activity='unknown'), dict(activity='spot.a7c2&activity=spot.f3b8')]
    for case in cases:
        with pytest.raises(HTTPException) as denied:
            require_learner_lesson_binding(request(app, **case))
        assert denied.value.status_code == 403


def test_file_source_rejects_other_owner_and_path_escape(local):
    private, _, context = local
    source = FileTrustedJournalSource(private/'bindings.json', ACTIVITIES[0])
    owner = context['owner']
    identity = VerifiedProviderIdentity(owner['provider_key'], owner['provider_subject'], 'csrf', 'origin')
    assert source.find_existing(identity)
    with pytest.raises(SchoolLessonOwnershipRejected):
        source.find_existing(VerifiedProviderIdentity('local-single-user', 'other', 'csrf', 'origin'))
    manifest = json.loads((private/'bindings.json').read_bytes())
    manifest['assignments'][ACTIVITIES[0]]['journal'] = '../outside.sqlite3'
    (private/'bindings.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(SchoolLessonOwnershipRejected):
        source.find_existing(identity)


def test_production_has_no_local_fallback(local, monkeypatch):
    private, app, _ = local
    with pytest.raises(HTTPException) as missing:
        require_learner_lesson_binding(request(FastAPI()))
    assert missing.value.status_code == 503
    monkeypatch.setenv('VERCEL_ENV', 'production')
    with pytest.raises(ValueError):
        create_local_app(private)
    with pytest.raises(HTTPException) as deployed:
        require_learner_lesson_binding(request(app))
    assert deployed.value.status_code in {403, 503}
