"""CLI-only synthetic admission to the already-tested school backend.

Not copied to the site. It never uses CMS admin identity or production records.
"""
import json
import os
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import bridge_school_api.school_spot_lesson as lesson


def dispatch(message):
    app = FastAPI()
    app.include_router(lesson.router)
    if os.environ.get('BRIDGE_YAN_FIXTURE_READY') == '1':
        # The local operator owns this fixture. These are not login credentials.
        principal = lesson.VerifiedSchoolSession('local-school', 'local-learner',
            'local-preview-csrf', os.environ.get('BRIDGE_YAN_ORIGIN', 'http://site.test'))
        binding = lesson.LearnerLessonBinding('local-school', 'local-learner',
            'yan-local-preview', Path(os.environ['BRIDGE_YAN_JOURNAL']))
        lesson.verify_school_browser_session = lambda request: principal
        lesson.lookup_existing_lesson_session = lambda verified: binding
    with TestClient(app, client=('127.0.0.1', 50000)) as client:
        response = client.request(message['method'], message['path'],
            headers=message['headers'], content=message['body'].encode('utf-8'))
    return {'status': response.status_code, 'headers': dict(response.headers),
            'body': response.text}


if __name__ == '__main__':
    print(json.dumps(dispatch(json.load(sys.stdin)), ensure_ascii=True))
