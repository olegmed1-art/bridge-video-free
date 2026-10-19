"""Three reviewed local staging lessons. No production registration or grading."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from html import escape
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import sqlite3
import uuid
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

ARCHIVE_SHA = '58ce6d33b91ce293fedbcea3837ee0d42b7d4f092d6c2795a90b9653ffda9f4a'
LIBRARY_ID = 'libfile_6c00056b423881918d16199f73b7a421'
LOADER_SHA = '910f09c9dcd658f6833670b1418f071707f0327bccb67fb80bf62481761a591e'
MANIFEST_SHA = '611ddf2c9555552b4cf81aa4e5a52ca4a1e89e2f7d50c16ab71f541392622469'
IDS = frozenset({'diana19-q-001', 'diana19-q-002', 'diana19-q-003'})
STATIC = Path(__file__).parent/'static/diana19'
HEADERS = {'Cache-Control': 'private, no-store, max-age=0',
    'X-Robots-Tag': 'noindex, nofollow',
    'Content-Security-Policy': "default-src 'self'; base-uri 'none'; frame-ancestors 'none'"}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def install_bundle(private: Path, archive: Path, owner_id: str):
    """Copy the exact accepted source into a new private directory, never web root."""
    private, archive = Path(private).resolve(), Path(archive).resolve()
    if digest(archive.read_bytes()) != ARCHIVE_SHA:
        raise ValueError('accepted lesson archive SHA mismatch')
    target = private/'diana19-bundle'
    if target.exists() or (private/'diana19-binding.json').exists():
        raise FileExistsError('staging bundle is already installed')
    with zipfile.ZipFile(archive) as source:
        if len(source.infolist()) != 145 or source.testzip() is not None:
            raise ValueError('accepted archive member count/CRC mismatch')
        names = set()
        for item in source.infolist():
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in item.filename
                    or ':' in item.filename or item.filename in names
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('unsafe accepted archive entry')
            names.add(item.filename)
        target.mkdir(mode=0o700)
        source.extractall(target)
    frozen = private/'diana19-source.zip'
    with frozen.open('xb') as output, archive.open('rb') as original:
        shutil.copyfileobj(original, output)
    bundle = DianaBundle(target)
    journal = private/'diana19-answers.sqlite3'
    DianaJournal.create(journal, owner_id, ARCHIVE_SHA)
    binding = {'schema': 'local-diana19-staging-binding-v1', 'scope': 'LOCAL_SINGLE_USER',
        'bundle': 'diana19-bundle', 'bundle_manifest_sha256': bundle.manifest_sha,
        'archive_sha256': ARCHIVE_SHA, 'journal': journal.name, 'owner_id': owner_id}
    with (private/'diana19-binding.json').open('x', encoding='utf-8') as output:
        json.dump(binding, output, indent=2)
        output.write('\n')
    return {'lessons': list(bundle.questions), 'status': 'STAGING', 'answers_created': 0}


class DianaBundle:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        # The installer admits only the SHA-pinned, reviewed source archive.
        if (digest((self.root/'load_lessons.py').read_bytes()) != LOADER_SHA or
                digest((self.root/'BUNDLE_MANIFEST.json').read_bytes()) != MANIFEST_SHA):
            raise ValueError('accepted loader/manifest changed')
        spec = importlib.util.spec_from_file_location('_diana19_accepted_loader', self.root/'load_lessons.py')
        loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader)
        loaded = loader.load_local_instructor_bundle(self.root)
        public = loader.load_client_questions(self.root)
        self.manifest_sha = digest((self.root/'BUNDLE_MANIFEST.json').read_bytes())
        self.questions = {item['lesson_id']: item for item in public['lessons']}
        if set(self.questions) != IDS or loaded['index']['local_staging_only'] is not True:
            raise ValueError('staging lesson set changed')
        self.explanations = {}
        drive_ids = {case['candidate']['payload'].get('source', {}).get('drive_file_id_claim')
                     for case in loaded['cases']} - {None}
        if len(drive_ids) != 1 or not re.fullmatch(r'[A-Za-z0-9_-]{10,120}', next(iter(drive_ids))):
            raise ValueError('source video link metadata unavailable')
        video_url = 'https://drive.google.com/file/d/'+next(iter(drive_ids))+'/view'
        for case in loaded['cases']:
            meta, payload = case['metadata'], case['candidate']['payload']
            lesson_id = meta['client_question_id']
            # Explicit projection, never serialize a candidate, trace, E/W or ASR.
            paragraphs = [payload[key] for key in ('teaching_point_ru', 'answer_ru', 'explanation_ru')
                          if isinstance(payload.get(key), str)]
            self.explanations[lesson_id] = {'paragraphs': paragraphs,
                'source': {'video_label': 'Диана19',
                    'video_url': video_url,
                    'video_link_status': 'SOURCE_METADATA_CLAIM',
                    'video_sha256': loaded['index']['video_sha256'],
                    'timecode_start': meta['asr_interval_label'][0],
                    'timecode_end': meta['asr_interval_label'][1],
                    'start_ms': meta['asr_interval_ms'][0],
                    'candidate_sha256': meta['candidate']['sha256'],
                    'audio_accuracy_verified': False, 'teacher_quote_verified': False},
                'scope_notice': 'Учебный разбор при указанных условиях. ASR не сверена с аудио; '
                    'это не подтверждённая цитата преподавателя. Полная сдача и непрерывный ход игры не проверены.'}

    def question_sha(self, lesson_id):
        return digest(json.dumps(self.questions[lesson_id], ensure_ascii=False,
                                 sort_keys=True, separators=(',', ':')).encode())


class DianaJournal:
    """One immutable response per lesson; idempotent retry; no inferred score."""
    def __init__(self, path: Path, owner_id: str, archive_sha=ARCHIVE_SHA):
        self.path, self.owner_id, self.archive_sha = Path(path).resolve(), owner_id, archive_sha
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError('existing private staging journal required')

    @classmethod
    def create(cls, path: Path, owner_id: str, archive_sha=ARCHIVE_SHA):
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb'):
            pass
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE context (schema TEXT, owner_id TEXT, archive_sha TEXT)')
            db.execute('INSERT INTO context VALUES (?, ?, ?)',
                       ('local-staging-answer-journal-v1', owner_id, archive_sha))
            db.execute('CREATE TABLE answers (lesson_id TEXT PRIMARY KEY, request_id TEXT UNIQUE, '
                       'question_sha TEXT NOT NULL, answer TEXT NOT NULL, saved_utc TEXT NOT NULL)')
        return cls(path, owner_id, archive_sha)

    def _connect(self):
        db = sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True, timeout=5)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT * FROM context').fetchall() != [
                    ('local-staging-answer-journal-v1', self.owner_id, self.archive_sha)]:
                raise ValueError('staging journal owner/source mismatch')
            return db
        except Exception:
            db.close()
            raise

    def saved(self, lesson_id):
        db = self._connect()
        try:
            row = db.execute('SELECT request_id,question_sha,answer,saved_utc FROM answers '
                             'WHERE lesson_id=?', (lesson_id,)).fetchone()
            if row is None:
                return None
            return {'answer_id': row[0], 'question_sha256': row[1], 'answer': row[2],
                    'saved_utc': row[3], 'status': 'SAVED_UNASSESSED', 'assessment': None}
        finally:
            db.close()

    def check_context(self):
        """Verify existing journal source/owner header without reading answers."""
        db = self._connect()
        db.close()

    def save(self, lesson_id, question_sha, message):
        if (type(message) is not dict or set(message) != {'request_id', 'question_sha256', 'answer'}
                or message['question_sha256'] != question_sha or type(message['answer']) is not str
                or not 1 <= len(message['answer'].strip()) <= 4000
                or len(message['answer']) > 4000 or type(message['request_id']) is not str):
            raise ValueError('invalid staging response')
        uuid.UUID(message['request_id'])
        db = self._connect()
        try:
            old = db.execute('SELECT request_id,question_sha,answer FROM answers WHERE lesson_id=?',
                             (lesson_id,)).fetchone()
            incoming = (message['request_id'], question_sha, message['answer'])
            if old is not None:
                if old != incoming:
                    raise ValueError('answer already saved; no rewrite after reveal')
            else:
                db.execute('INSERT INTO answers VALUES (?, ?, ?, ?, ?)',
                           (lesson_id, *incoming, datetime.now(timezone.utc).isoformat()))
            db.commit()
        finally:
            db.close()
        return self.saved(lesson_id)


class DianaStagingService:
    def __init__(self, bundle, journal):
        self.bundle, self.journal = bundle, journal

    @classmethod
    def from_private(cls, private: Path, owner_id: str):
        private = Path(private).resolve()
        base = cls._from_three_lesson_private(private, owner_id) if (
            private/'diana19-binding.json').is_file() else None
        if (private/'diana19-visible-binding.json').is_file():
            from .diana19_visible_stages import VisibleStageService
            return VisibleStageService.from_private(private, owner_id, base)
        if base is None:
            raise ValueError('existing accepted lesson binding required')
        return base

    @classmethod
    def _from_three_lesson_private(cls, private: Path, owner_id: str):
        binding = json.loads((private/'diana19-binding.json').read_bytes())
        if (binding.get('schema') != 'local-diana19-staging-binding-v1'
                or binding.get('scope') != 'LOCAL_SINGLE_USER' or binding.get('owner_id') != owner_id
                or binding.get('archive_sha256') != ARCHIVE_SHA
                or binding.get('bundle') != 'diana19-bundle'
                or binding.get('journal') != 'diana19-answers.sqlite3'):
            raise ValueError('staging context rejected')
        bundle = DianaBundle(private/binding['bundle'])
        if bundle.manifest_sha != binding['bundle_manifest_sha256']:
            raise ValueError('staging source manifest changed')
        return cls(bundle, DianaJournal(private/binding['journal'], owner_id))

    def catalog(self):
        return {'status': 'STAGING', 'local_single_user': True, 'canonical_promotion_allowed': False,
            'lessons': [{'lesson_id': item['lesson_id'], 'title_ru': item['title_ru']}
                        for item in self.bundle.questions.values()]}

    def view(self, lesson_id):
        if lesson_id not in self.bundle.questions:
            raise KeyError('unknown lesson')
        saved = self.journal.saved(lesson_id)
        sha = self.bundle.question_sha(lesson_id)
        if saved is not None and saved['question_sha256'] != sha:
            raise ValueError('saved question source changed')
        return {'status': 'STAGING', 'question': self.bundle.questions[lesson_id],
            'question_sha256': sha, 'saved': saved,
            'explanation': self.bundle.explanations[lesson_id] if saved is not None else None,
            'automatic_assessment': False, 'canonical_promotion_allowed': False}

    def submit(self, lesson_id, message):
        if lesson_id not in self.bundle.questions:
            raise KeyError('unknown lesson')
        self.journal.save(lesson_id, self.bundle.question_sha(lesson_id), message)
        return self.view(lesson_id)


def require_staging(request: Request):
    app = request.app
    service = getattr(app.state, 'diana19_service', None)
    resolver = getattr(app.state, 'local_context_resolver', None)
    if (getattr(app.state, 'school_spot_mode', None) != 'LOCAL_SINGLE_USER'
            or os.environ.get('VERCEL_ENV') or not isinstance(service, DianaStagingService)
            or resolver is None):
        raise HTTPException(status_code=503)
    context = resolver.check_context(request)
    if (request.headers.get('x-learner-id') is not None or request.headers.get('x-school-id') is not None
            or context['owner']['provider_subject'] != service.journal.owner_id):
        raise HTTPException(status_code=403)
    if request.method not in {'GET', 'HEAD'} and not (
            secrets.compare_digest(request.headers.get('origin', ''), context['frontend_origin'])
            and secrets.compare_digest(request.headers.get('x-csrf-token', ''), context['csrf_nonce'])):
        raise HTTPException(status_code=403)
    request.state.diana19_csrf = context['csrf_nonce']
    return service


router = APIRouter(prefix='/school/lessons/diana19', include_in_schema=False)


@router.get('/catalog')
def catalog(service=Depends(require_staging)):
    return JSONResponse(service.catalog(), headers=HEADERS)


@router.get('/lesson/{lesson_id}')
def lesson(lesson_id: str, service=Depends(require_staging)):
    try:
        return JSONResponse(service.view(lesson_id), headers=HEADERS)
    except KeyError:
        raise HTTPException(status_code=404)


@router.post('/answer/{lesson_id}')
async def answer(lesson_id: str, request: Request, service=Depends(require_staging)):
    body = await request.body()
    if len(body) > 20000:
        raise HTTPException(status_code=413)
    try:
        result = service.submit(lesson_id, json.loads(body))
    except KeyError:
        raise HTTPException(status_code=404)
    except (ValueError, TypeError, sqlite3.IntegrityError):
        raise HTTPException(status_code=409)
    return JSONResponse(result, headers=HEADERS)


@router.get('/')
@router.get('/assets/{asset}')
def page(request: Request, asset: str = '', service=Depends(require_staging)):
    if not asset:
        content = (STATIC/'index.html').read_text(encoding='utf-8').replace('<head>',
            '<head>\n<meta name="bridge-school-csrf-token" content="'+
            escape(request.state.diana19_csrf, quote=True)+'">', 1)
        return HTMLResponse(content, headers=HEADERS)
    if asset == 'styles.css':
        return FileResponse(Path(__file__).parent/'static/spot_cycle_session/styles.css', headers=HEADERS)
    if asset not in {'diana19.js', 'diana19.css'}:
        raise HTTPException(status_code=404)
    return FileResponse(STATIC/asset, headers=HEADERS)
