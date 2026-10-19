"""Explicit single-user process context, never a website visitor identity.

Only the dedicated local launcher installs this resolver. Normal app.py does
not read these files or enable this mode from environment variables.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request

from .local_spot_session import load_store_class
from .school_spot_lesson import LearnerLessonBinding, VerifiedSchoolSession, router
from .school_spot_repository import FileTrustedJournalSource, VerifiedProviderIdentity

ACTIVITIES = ('spot.a7c2', 'spot.f3b8')


def local_origin(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname != '127.0.0.1' or url.username or url.password
            or url.path or url.query or url.fragment or url.port is None
            or not 1024 <= url.port <= 65535):
        raise ValueError('literal unprivileged loopback origin required')
    return value


def _write_new(path: Path, data):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                   'w', encoding='utf-8') as output:
        json.dump(data, output, ensure_ascii=False, indent=2)
        output.write('\n')


def create_assignments(private_dir: Path, frontend_origin: str, backend_origin: str):
    """New assignments only, through the existing create API; no learner answers."""
    private_dir = Path(private_dir).resolve()
    frontend_origin, backend_origin = local_origin(frontend_origin), local_origin(backend_origin)
    if frontend_origin == backend_origin or private_dir.exists():
        raise ValueError('new private directory and two different local origins required')
    private_dir.mkdir(parents=True, mode=0o700)
    (private_dir / 'journals').mkdir(mode=0o700)
    owner_id = str(uuid.uuid4())
    owner = {'provider_key': 'local-single-user', 'provider_subject': owner_id,
             'school_id': 'local-only', 'student_id': owner_id}
    assignments = {}
    store_class = load_store_class()
    for exercise_id in ACTIVITIES:
        session_id = 'local-' + str(uuid.uuid4())
        relative = 'journals/' + exercise_id + '.sqlite3'
        store = store_class.create(private_dir / relative, session_id,
                                   preview_exercise_id=exercise_id)
        if store.resume()['revision'] != 1:
            raise ValueError('new assignment is not empty')
        assignments[exercise_id] = {'exercise_id': exercise_id,
                                   'session_id': session_id, 'journal': relative}
    _write_new(private_dir / 'bindings.json', {'schema': 'school-file-journal-bindings-v1',
        'scope': 'LOCAL_SINGLE_USER', 'owner': owner, 'assignments': assignments,
        'created_utc': datetime.now(timezone.utc).isoformat()})
    _write_new(private_dir / 'runtime.json', {'schema': 'local-single-user-runtime-v1',
        'mode': 'LOCAL_SINGLE_USER', 'owner': owner, 'frontend_origin': frontend_origin,
        'backend_origin': backend_origin, 'csrf_nonce': secrets.token_urlsafe(32)})
    return {'private_dir': str(private_dir), 'activities': list(assignments),
            'answer_records_created': 0, 'mode': 'LOCAL_SINGLE_USER'}


def read_context(private_dir: Path):
    private_dir = Path(private_dir).resolve()
    path = private_dir / 'runtime.json'
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ValueError('invalid local runtime context')
    context = json.loads(path.read_bytes())
    if (type(context) is not dict or context.get('schema') != 'local-single-user-runtime-v1'
            or context.get('mode') != 'LOCAL_SINGLE_USER'
            or type(context.get('owner')) is not dict
            or context['owner'].get('provider_key') != 'local-single-user'
            or not all(type(context['owner'].get(k)) is str and 1 <= len(context['owner'][k]) <= 120
                       for k in ('provider_subject', 'school_id', 'student_id'))
            or context['owner']['school_id'] != 'local-only'
            or not isinstance(context.get('csrf_nonce'), str)
            or len(context['csrf_nonce']) < 32):
        raise ValueError('invalid local runtime context')
    local_origin(context['frontend_origin'])
    local_origin(context['backend_origin'])
    if context['frontend_origin'] == context['backend_origin']:
        raise ValueError('two local origins required')
    return context


class LocalSingleUserResolver:
    def __init__(self, private_dir: Path):
        self.private_dir = Path(private_dir).resolve()
        self.context = read_context(self.private_dir)

    def check_context(self, request: Request):
        context = self.context
        if (os.environ.get('VERCEL_ENV') or request.client is None
                or request.client.host != '127.0.0.1'
                or request.headers.get('host') != urlsplit(context['backend_origin']).netloc
                or any(name in request.headers for name in
                       ('forwarded', 'x-forwarded-for', 'x-forwarded-host'))
                or request.headers.get('origin', context['frontend_origin']) != context['frontend_origin']):
            raise HTTPException(status_code=403, detail='local context rejected')
        return context

    def __call__(self, request: Request):
        context = self.check_context(request)
        choices = request.query_params.getlist('activity')
        if len(choices) > 1:
            raise HTTPException(status_code=403, detail='activity binding rejected')
        exercise_id = choices[0] if choices else ACTIVITIES[0]
        if exercise_id not in ACTIVITIES:
            raise HTTPException(status_code=403, detail='activity binding rejected')
        owner = context['owner']
        identity = VerifiedProviderIdentity(owner['provider_key'], owner['provider_subject'],
            context['csrf_nonce'], context['frontend_origin'])
        source = FileTrustedJournalSource(self.private_dir / 'bindings.json', exercise_id)
        record = source.find_existing(identity)
        if (record is None or record.school_id != owner['school_id']
                or record.student_id != owner['student_id']):
            raise HTTPException(status_code=403, detail='local owner binding rejected')
        request.state.school_spot_assigned_ids = source.assigned_ids(identity)
        return (VerifiedSchoolSession(record.school_id, record.student_id,
                    context['csrf_nonce'], context['frontend_origin'], 'LOCAL_SINGLE_USER'),
                LearnerLessonBinding(record.school_id, record.student_id,
                    record.session_id, record.journal_path))


def create_local_app(private_dir: Path) -> FastAPI:
    if os.environ.get('VERCEL_ENV'):
        raise ValueError('local single-user app cannot run in a deployed environment')
    app = FastAPI(title='Local single-user bridge lesson')
    app.state.school_spot_mode = 'LOCAL_SINGLE_USER'
    app.state.school_spot_binding_resolver = LocalSingleUserResolver(private_dir)
    app.state.local_context_resolver = app.state.school_spot_binding_resolver
    app.include_router(router)
    if any((Path(private_dir)/name).is_file() for name in (
            'diana19-binding.json', 'diana19-visible-binding.json')):
        from .diana19_staging import DianaStagingService, router as diana_router
        owner_id = app.state.local_context_resolver.context['owner']['provider_subject']
        app.state.diana19_service = DianaStagingService.from_private(Path(private_dir), owner_id)
        app.include_router(diana_router)
    return app
