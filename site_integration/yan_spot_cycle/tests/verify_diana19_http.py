"""Linux real PHP/HTTP adapter check using neutral, non-private protocol data."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid

from bridge_school_api.diana19_staging import DianaJournal
from bridge_school_api.local_single_user_lesson import read_context
from site_integration.yan_spot_cycle.build_candidate import HERE, REPO
from site_integration.yan_spot_cycle.create_local import create
from site_integration.yan_spot_cycle.verify_persistent import ports, stop
from .diana19_protocol_fixture import FIXTURE_SOURCE


def verify(root):
    if sys.platform != 'linux':
        raise RuntimeError('HTTP verification is Linux-only; no Windows socket retry')
    front_port, back_port = ports()
    frontend, backend = f'http://127.0.0.1:{front_port}', f'http://127.0.0.1:{back_port}'
    create(root, frontend, backend)
    private = root/'private'
    owner = read_context(private)['owner']['provider_subject']
    journal = DianaJournal.create(private/'protocol-answers.sqlite3', owner, FIXTURE_SOURCE)
    env = dict(os.environ, PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1',
        BRIDGE_LOCAL_SINGLE_USER='1', BRIDGE_YAN_CANDIDATE=str(root/'site'),
        BRIDGE_SINGLE_USER_FRONTEND_ORIGIN=frontend, BRIDGE_SINGLE_USER_BACKEND_ORIGIN=backend)
    command = [sys.executable, '-m', 'site_integration.yan_spot_cycle.tests.diana19_protocol_fixture',
               '--private', str(private), '--port', str(back_port)]
    def launch():
        return subprocess.Popen(command, cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def url(suffix=''):
        return frontend+'/index.php?'+urllib.parse.urlencode({'route': 'practice/diana19'+suffix})
    def request(suffix='', body=None, headers=None):
        from urllib.error import HTTPError
        try:
            with urllib.request.urlopen(urllib.request.Request(url(suffix),
                data=None if body is None else json.dumps(body).encode(), headers=headers or {}), timeout=5) as response:
                return response.status, response.read().decode()
        except HTTPError as error:
            return error.code, error.read().decode()
    def ready(process):
        import time
        deadline = time.monotonic()+20
        while process.poll() is None and time.monotonic()<deadline:
            try:
                status, body = request()
                if status == 200:
                    return body
            except OSError:
                pass
            time.sleep(0.1)
        raise AssertionError('protocol HTTP servers not ready')
    back = front = None
    saved = {}
    try:
        back = launch()
        front = subprocess.Popen(['php', '-S', f'127.0.0.1:{front_port}', str(HERE/'local_router.php')],
            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = ready(back)
        assert 'diana19-host' in page
        assert 'practice%2Fdiana19%2Fassets%2Fdiana19.js' in page
        csrf = re.search(r'name="bridge-school-csrf-token" content="([^"]+)"', page)[1]
        headers = {'Origin': frontend, 'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}
        assert request('/assets/diana19.js')[0] == 200
        assert request('/assets/server/lesson-index.json')[0] == 404
        assert request('/server/lesson-index.json')[0] == 404
        assert request('/catalog', headers={'X-Learner-Id': 'other'})[0] == 403
        assert request('/catalog', headers={'Origin': 'http://evil.invalid'})[0] == 403
        status, catalog = request('/catalog')
        assert status == 200 and len(json.loads(catalog)['lessons']) == 3
        for number in (1, 2, 3):
            key = f'diana19-q-00{number}'
            status, body = request('/lesson/'+key)
            view = json.loads(body)
            assert status == 200 and view['saved'] is None and view['explanation'] is None
            assert 'POST_SAVE_ONLY_PROTOCOL_MARKER' not in body
            message = {'request_id': str(uuid.uuid4()), 'question_sha256': view['question_sha256'],
                       'answer': 'NEW automated neutral protocol answer.'}
            assert request('/answer/'+key, message)[0] == 403
            status, body = request('/answer/'+key, message, headers)
            assert status == 200
            result = json.loads(body)
            assert result['saved']['answer'] == message['answer']
            assert result['saved']['assessment'] is None
            assert result['explanation']['paragraphs'] == ['POST_SAVE_ONLY_PROTOCOL_MARKER']
            assert request('/answer/'+key, message, headers) == (status, body)
            saved[key] = result
        before = hashlib.sha256(journal.path.read_bytes()).hexdigest()
        stop(back)
        back = launch()
        ready(back)
        for key in saved:
            status, body = request('/lesson/'+key)
            assert status == 200 and json.loads(body) == saved[key]
        assert hashlib.sha256(journal.path.read_bytes()).hexdigest() == before
    finally:
        stop(front)
        stop(back)
    artifact = root/'artifact'
    artifact.mkdir()
    shutil.copyfile(journal.path, artifact/'protocol-answers.sqlite3')
    report = {'schema': 'diana19-http-protocol-verification-v1', 'status': 'PASS',
        'cases': 3, 'transport': 'REAL_PHP_HTTP_UVICORN_SQLITE', 'backend_restarted': True,
        'persisted_answers_reloaded': True, 'actual_reviewed_materials_in_this_ci': False,
        'fixture_source': FIXTURE_SOURCE, 'private_server_materials_uploaded': False,
        'automatic_assessment': False, 'windows_http_verified': False, 'journal_sha256': before}
    (artifact/'HTTP_PROTOCOL_EVIDENCE.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    verify(parser.parse_args().output.resolve())
