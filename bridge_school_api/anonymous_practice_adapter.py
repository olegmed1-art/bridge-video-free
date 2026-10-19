"""Explicit anonymous-session admission for the existing Diana19 lesson router.

No account, token, journal or PHP session is created here. A trusted host must
supply its established native-session/CSRF contract. Empty config fails closed.
This module is never installed by app.py or the existing local single-user app.
"""
from dataclasses import dataclass, field
import secrets
import sqlite3
from typing import Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request

from .diana19_staging import DianaJournal, DianaStagingService, require_staging, router


class AnonymousSessionUnavailable(ValueError):
    """Trusted host reports an absent or expired first-party browser session."""


@dataclass(frozen=True)
class AnonymousPracticeBinding:
    owner_ref: str
    csrf_token: str
    origin: str
    service: DianaStagingService
    kind: str = 'ANONYMOUS_PRACTICE'


@dataclass(frozen=True)
class AnonymousPracticeConfig:
    enabled: bool = False
    resolve_session: Callable[[Request], AnonymousPracticeBinding] | None = None
    allowed_origins: frozenset[str] = field(default_factory=frozenset)


def valid_origin(value):
    if type(value) is not str:
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return bool(parsed.hostname and not (parsed.username or parsed.password or parsed.path or
        parsed.query or parsed.fragment) and (parsed.scheme == 'https' or
        parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'))


def anonymous_dependency(config: AnonymousPracticeConfig):
    def admit(request: Request):
        if (type(config) is not AnonymousPracticeConfig or config.enabled is not True
                or not callable(config.resolve_session) or type(config.allowed_origins) is not frozenset
                or not config.allowed_origins
                or not all(valid_origin(origin) for origin in config.allowed_origins)):
            raise HTTPException(status_code=503, detail='anonymous practice contract unavailable')
        if any(header in request.headers for header in (
                'x-learner-id', 'x-school-id', 'x-owner-id', 'x-anonymous-owner')):
            raise HTTPException(status_code=403, detail='browser ownership header rejected')
        try:
            binding = config.resolve_session(request)
        except AnonymousSessionUnavailable as exc:
            raise HTTPException(status_code=403, detail='browser session unavailable') from exc
        if (type(binding) is not AnonymousPracticeBinding or binding.kind != 'ANONYMOUS_PRACTICE'
                or type(binding.owner_ref) is not str or not 1 <= len(binding.owner_ref) <= 128
                or type(binding.csrf_token) is not str or not 1 <= len(binding.csrf_token) <= 512
                or type(binding.origin) is not str or binding.origin not in config.allowed_origins
                or not isinstance(binding.service, DianaStagingService)
                or not isinstance(binding.service.journal, DianaJournal)):
            raise HTTPException(status_code=503, detail='anonymous practice contract invalid')
        if binding.service.journal.owner_id != binding.owner_ref:
            raise HTTPException(status_code=403, detail='anonymous journal owner rejected')
        supplied_origin = request.headers.get('origin')
        if supplied_origin is not None and supplied_origin != binding.origin:
            raise HTTPException(status_code=403, detail='practice origin rejected')
        if request.method not in {'GET', 'HEAD'}:
            try:
                accepted = secrets.compare_digest(supplied_origin or '', binding.origin) and \
                    secrets.compare_digest(request.headers.get('x-csrf-token', ''), binding.csrf_token)
            except TypeError:
                accepted = False
            if not accepted:
                raise HTTPException(status_code=403, detail='practice CSRF rejected')
        try:
            binding.service.journal.check_context()
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            raise HTTPException(status_code=403, detail='anonymous journal context rejected') from exc
        request.state.diana19_csrf = binding.csrf_token
        request.state.diana19_owner_kind = 'ANONYMOUS_PRACTICE'
        return binding.service
    return admit


def install_anonymous_practice_adapter(app: FastAPI, config: AnonymousPracticeConfig):
    """Explicit host installation only. Default config registers a closed route."""
    app.dependency_overrides[require_staging] = anonymous_dependency(config)
    app.include_router(router)
    app.state.diana19_access_mode = 'ANONYMOUS_PRACTICE'


def create_anonymous_practice_app(config: AnonymousPracticeConfig | None = None):
    app = FastAPI(title='Anonymous practice application adapter')
    install_anonymous_practice_adapter(app, config or AnonymousPracticeConfig())
    return app
