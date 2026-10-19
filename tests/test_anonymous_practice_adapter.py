"""New adapter contract only. No HTTP, Oracle/DC, credentials or account setup."""
from types import SimpleNamespace

from fastapi import HTTPException
import pytest
from starlette.requests import Request

from bridge_school_api.anonymous_practice_adapter import (
    AnonymousPracticeBinding, AnonymousPracticeConfig, AnonymousSessionUnavailable,
    anonymous_dependency, create_anonymous_practice_app,
)
from bridge_school_api.diana19_staging import DianaJournal, DianaStagingService, require_staging


def request(*, method='GET', headers=()):
    return Request({'type': 'http', 'method': method, 'path': '/', 'scheme': 'https',
        'headers': [(k.encode(), v.encode()) for k, v in headers],
        'client': ('127.0.0.1', 50000), 'server': ('practice.invalid', 443)})


@pytest.fixture
def bound(tmp_path):
    owner = 'anonymous-protocol-fixture-owner'
    service = DianaStagingService(SimpleNamespace(questions={}),
        DianaJournal.create(tmp_path/'anonymous-fixture.sqlite3', owner))
    binding = AnonymousPracticeBinding(owner, 'existing-fixture-csrf', 'https://practice.invalid', service)
    config = AnonymousPracticeConfig(True, lambda req: binding, frozenset({binding.origin}))
    return service, binding, config


def test_default_config_is_closed_and_creates_no_journal(tmp_path, monkeypatch):
    def unexpected_create(*args, **kwargs):
        raise AssertionError('adapter must not create journals or accounts')
    monkeypatch.setattr(DianaJournal, 'create', unexpected_create)
    app = create_anonymous_practice_app()
    assert require_staging in app.dependency_overrides
    with pytest.raises(HTTPException) as denied:
        app.dependency_overrides[require_staging](request())
    assert denied.value.status_code == 503


def test_explicit_anonymous_contract_needs_no_learner_or_school_role(bound):
    service, binding, config = bound
    req = request()
    assert anonymous_dependency(config)(req) is service
    assert req.state.diana19_owner_kind == 'ANONYMOUS_PRACTICE'
    assert req.state.diana19_csrf == binding.csrf_token


def test_write_checks_and_spoofed_owner_headers_are_preserved(bound):
    service, binding, config = bound
    admit = anonymous_dependency(config)
    with pytest.raises(HTTPException) as missing:
        admit(request(method='POST'))
    assert missing.value.status_code == 403
    assert admit(request(method='POST', headers=[('origin', binding.origin),
        ('x-csrf-token', binding.csrf_token)])) is service
    for header in ('x-owner-id', 'x-anonymous-owner', 'x-learner-id', 'x-school-id'):
        with pytest.raises(HTTPException) as spoofed:
            admit(request(headers=[(header, binding.owner_ref)]))
        assert spoofed.value.status_code == 403


def test_missing_session_wrong_owner_and_unknown_origin_stay_closed(bound):
    service, binding, config = bound
    def missing(req):
        raise AnonymousSessionUnavailable()
    with pytest.raises(HTTPException) as absent:
        anonymous_dependency(AnonymousPracticeConfig(True, missing, config.allowed_origins))(request())
    assert absent.value.status_code == 403
    service.journal.owner_id = 'other-owner'
    with pytest.raises(HTTPException) as wrong:
        anonymous_dependency(config)(request())
    assert wrong.value.status_code == 403
    with pytest.raises(HTTPException) as origin:
        anonymous_dependency(AnonymousPracticeConfig(True, config.resolve_session,
            frozenset({'http://not-loopback.invalid'})))(request())
    assert origin.value.status_code == 503
