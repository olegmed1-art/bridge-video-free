"""Explicit Linux HTTP fixture; never installed by app.py or deployed config.

PHP projects its own native session into a private test directory. This tests
the existing anonymous admission seam without inventing Yan's CMS contract.
No author material, user account, external identity or production data is used.
"""
import argparse
import hashlib
from http.cookies import SimpleCookie
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

import uvicorn

from bridge_school_api.anonymous_practice_adapter import (
    AnonymousPracticeBinding, AnonymousPracticeConfig, AnonymousSessionUnavailable,
    create_anonymous_practice_app,
)
from bridge_school_api.diana19_staging import DianaJournal, DianaStagingService
from bridge_school_api.diana19_visible_stages import VisibleStageService
from bridge_school_api.school_spot_lesson import router as closed_spot_router
from .diana19_protocol_fixture import fixture_bundle

FIXTURE_SOURCE = 'NEUTRAL_NATIVE_PHP_SESSION_PROTOCOL_NOT_YAN_CMS_OR_REVIEWED_CONTENT'


def fixture_entries():
    entries = []
    for suffix in ('001', '002'):
        eid = 'diana19-visible-exercise-'+suffix
        question = {'lesson_id': eid, 'title_ru': 'Neutral staged fixture '+suffix,
            'question_ru': 'Record a new independent automated protocol response.',
            'conditions_ru': ['Synthetic protocol data; not a reviewed bridge exercise.'],
            'visible_holdings': {'N': ['2C']}, 'known_play': [], 'options': []}
        stages = [{'stage_id': 'initial', 'assistance': 'INDEPENDENT_RESPONSE', 'question': question}]
        if suffix == '001':
            stages.append({'stage_id': 'followup', 'assistance': 'GUIDED_PRACTICE',
                'question': {**question, 'question_ru': 'FIXTURE_GUIDED_ONLY_AFTER_FIRST_COMMIT'}})
        entries.append({'exercise_id': eid, 'title_ru': question['title_ru'], 'stages': stages,
            'explanation': {'paragraphs': ['FIXTURE_EXPLANATION_ONLY_AFTER_FINAL_COMMIT'],
                'source': {'video_url': None, 'timecode_start': '00:00:01.000',
                           'timecode_end': '00:00:02.000'},
                'scope_notice': 'Neutral protocol fixture; no claim about source video or teaching.'}})
    return entries


def app_for(private):
    private = Path(private).resolve()
    context = json.loads((private/'fixture-context.json').read_bytes())
    if context['schema'] != 'native-session-test-context-v1':
        raise ValueError('explicit test context required')
    def resolve(request):
        if (request.client is None or request.client.host != '127.0.0.1'
                or request.headers.get('host') != urlsplit(context['backend_origin']).netloc
                or any(key in request.headers for key in ('forwarded', 'x-forwarded-for', 'x-forwarded-host'))):
            raise AnonymousSessionUnavailable('test channel rejected')
        cookies = SimpleCookie()
        try:
            cookies.load(request.headers.get('cookie', ''))
            sid = cookies['PHPSESSID'].value
        except (KeyError, ValueError):
            raise AnonymousSessionUnavailable('native test session missing')
        if not re.fullmatch(r'[A-Za-z0-9,-]{16,128}', sid):
            raise AnonymousSessionUnavailable('native test session invalid')
        key = hashlib.sha256(sid.encode()).hexdigest()
        source = private/'session-projections'/f'{key}.json'
        try:
            if source.is_symlink() or source.stat().st_size > 2048:
                raise ValueError('invalid test projection')
            projection = json.loads(source.read_bytes())
        except (OSError, ValueError):
            raise AnonymousSessionUnavailable('native test session was not projected by PHP')
        if (projection.get('schema') != 'native-php-fixture-owner-v1'
                or projection.get('session_sha256') != key
                or projection.get('origin') != context['frontend_origin']
                or not re.fullmatch(r'[a-f0-9]{64}', projection.get('owner_ref', ''))
                or not re.fullmatch(r'[a-f0-9]{64}', projection.get('csrf_token', ''))):
            raise AnonymousSessionUnavailable('native test projection rejected')
        owner = projection['owner_ref']
        # Test-only provisioning of NEW automated records. Production adapter
        # still creates no account, session or journal and stays disabled.
        def journal(label):
            path = private/'journals'/f'{owner}-{label}.sqlite3'
            if not path.exists():
                return DianaJournal.create(path, owner, FIXTURE_SOURCE)
            return DianaJournal(path, owner, FIXTURE_SOURCE)
        base = DianaStagingService(fixture_bundle(), journal('three'))
        service = VisibleStageService(fixture_entries(), journal('visible'), base)
        return AnonymousPracticeBinding(owner, projection['csrf_token'], projection['origin'], service)
    app = create_anonymous_practice_app(AnonymousPracticeConfig(
        enabled=True, resolve_session=resolve,
        allowed_origins=frozenset({context['frontend_origin']})))
    # Its real default remains 503: this fixture does not bind the school route.
    app.include_router(closed_spot_router)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app_for(args.private), host='127.0.0.1', port=args.port,
                proxy_headers=False, access_log=False, log_level='warning')
