"""Neutral HTTP protocol data, not the reviewed private three-lesson package."""
import argparse
from pathlib import Path
from types import SimpleNamespace

import uvicorn

from bridge_school_api.diana19_staging import DianaJournal, DianaStagingService
from bridge_school_api.local_single_user_lesson import create_local_app, read_context


FIXTURE_SOURCE = 'SYNTHETIC_PROTOCOL_FIXTURE_NOT_REVIEWED_LESSON'


def fixture_bundle():
    questions = {f'diana19-q-00{i}': {'lesson_id': f'diana19-q-00{i}',
        'title_ru': f'Neutral protocol question {i}',
        'question_ru': 'Enter a new automated protocol response. This is not teaching content.',
        'conditions_ru': ['Synthetic protocol fixture only.'], 'visible_holdings': {'N': ['2C']},
        'known_play': [], 'options': []} for i in (1, 2, 3)}
    explanations = {key: {'paragraphs': ['POST_SAVE_ONLY_PROTOCOL_MARKER'],
        'source': {'video_url': 'https://drive.google.com/file/d/synthetic-fixture/view',
            'timecode_start': '00:00:01.000', 'timecode_end': '00:00:02.000'},
        'scope_notice': 'Synthetic fixture; not ASR or reviewed content.'} for key in questions}
    return SimpleNamespace(questions=questions, explanations=explanations,
                           question_sha=lambda lesson_id: 'a'*64)


def app_for(private):
    app = create_local_app(private)
    context = read_context(private)
    from bridge_school_api.diana19_staging import router
    app.state.diana19_service = DianaStagingService(fixture_bundle(),
        DianaJournal(private/'protocol-answers.sqlite3', context['owner']['provider_subject'], FIXTURE_SOURCE))
    app.include_router(router)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--private', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(app_for(args.private), host='127.0.0.1', port=args.port,
                proxy_headers=False, access_log=False, log_level='warning')
