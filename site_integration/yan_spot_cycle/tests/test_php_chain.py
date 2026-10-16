"""Only the new PHP -> existing school backend integration chain."""
import importlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from site_integration.yan_spot_cycle.build_candidate import build, original, HOOK
from bridge_school_api.local_spot_session import load_store_class

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]


@pytest.fixture
def candidate(tmp_path):
    target = tmp_path / 'site'
    build(target)
    # No archive bootstrap/configuration or SQL is in this executable tree.
    assert not (target / 'cms').exists()
    assert not list(target.rglob('*.sql'))
    index = (target / 'index.php').read_bytes()
    hook = HOOK.encode().replace(b'\n', b'\r\n' if b'\r\n' in index else b'\n')
    assert index.replace(hook, b'', 1) == original('index.php.txt')
    assert (target / '.htaccess').read_bytes() == original('htaccess.txt')
    return target


def request(candidate, journal, route='', *, method='GET', body=None, headers=None,
            dispatch=True, admitted=True):
    env = os.environ.copy()
    env.update(BRIDGE_YAN_CANDIDATE=str(candidate), BRIDGE_TEST_PYTHON=sys.executable,
        BRIDGE_YAN_JOURNAL=str(journal), BRIDGE_YAN_DISPATCH=str(int(dispatch)),
        BRIDGE_YAN_FIXTURE_READY=str(int(admitted)), PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=str(REPO))
    completed = subprocess.run(['php', str(HERE / 'php_driver.php'),
        'practice/spot-cycle' + route, method, json.dumps(headers or {})],
        input='' if body is None else json.dumps(body), encoding='utf-8',
        capture_output=True, cwd=REPO, env=env, timeout=15)
    assert completed.returncode == 0, completed.stderr
    statuses = re.findall(r'BRIDGE_STATUS=(\d+)', completed.stderr)
    assert statuses, completed.stderr
    return int(statuses[-1]), completed.stdout


def test_missing_host_dispatch_and_unverified_backend_stay_closed(candidate, tmp_path):
    for dispatch, admitted in [(False, False), (True, False)]:
        for suffix in ['', '/assets/learner-presentation.json', '/saved-answer', '/transport']:
            status, body = request(candidate, tmp_path / 'absent.sqlite3', suffix,
                method='POST' if suffix == '/transport' else 'GET',
                body={'operation': 'resume', 'value': {}} if suffix == '/transport' else None,
                dispatch=dispatch, admitted=admitted)
            assert status == 503
            assert 'cards' not in body and 'saved_answer' not in body
    assert request(candidate, tmp_path / 'absent', '/assets/../../config.php')[0] == 404
    assert request(candidate, tmp_path / 'absent', '/transport', method='GET')[0] == 405


@pytest.mark.parametrize('spot,first_play,count', [
    ('spot.a7c2', 'DA', 1), ('spot.a7c2', 'DK', 1), ('spot.a7c2', 'CA', 1),
    ('spot.f3b8', 'CA', 0), ('spot.f3b8', 'HA', 0),
])
def test_php_page_assets_and_acknowledged_answer(candidate, tmp_path, spot, first_play, count):
    journal = tmp_path / 'lesson.sqlite3'
    store = load_store_class().create(journal, 'yan-local-preview', preview_exercise_id=spot)
    status, page = request(candidate, journal)
    assert status == 200
    assert 'id="bridge-lesson-host"' in page
    assert 'index.php?route=practice%2Fspot-cycle%2Fassets%2Fsession-host.js' in page
    assert 'bridge-school-csrf-token' in page
    status, public = request(candidate, journal, '/assets/learner-presentation.json')
    assert status == 200
    assert len(json.loads(public)['cards']) == 2
    assert not any(key in public for key in ['expected_solution', 'teacher_version', 'answer_key'])
    status, host = request(candidate, journal, '/assets/session-host.js')
    assert status == 200 and 'bridge-lesson-host' in host
    assert request(candidate, journal, '/assets/lesson.teacher.json')[0] == 404
    assert request(candidate, journal, '/saved-answer')[0] == 409
    spoof = {'HTTP_X_LEARNER_ID': 'other'}
    assert request(candidate, journal, headers=spoof)[0] == 403
    assert request(candidate, journal, '/transport', method='POST',
        body={'operation': 'resume', 'value': {}})[0] == 403
    headers = {'HTTP_ORIGIN': 'http://site.test', 'HTTP_X_CSRF_TOKEN': 'local-preview-csrf'}

    def call(operation, value):
        status, body = request(candidate, journal, '/transport', method='POST',
            body={'operation': operation, 'value': value}, headers=headers)
        assert status == 200, body
        return json.loads(body)

    def command(kind, payload=None):
        state = call('resume', {})
        assignment = state['current_assignment']
        value = {'command': {'command_id': f"{kind}-{state['revision']}", 'kind': kind,
            'issuance_id': assignment['issuance_id'], 'payload': payload},
            'expected_revision': state['revision']}
        return call('execute', value)

    def ack(delivery):
        return call('acknowledge', {k: delivery[k] for k in ['delivery_id', 'result_sha256']})

    entry = call('resume', {})['current_assignment']
    step = importlib.import_module('_local_spot_candidate.teaching_step')
    expected = step.exercise_version_fields('entry.e1')['exercise_version']['expected_solution']['expected_answers']
    ack(command('RESPONSE', {'schema': 'low-card-structured-response-v1', 'exercise_id': 'entry.e1',
        'prompt_sha256': entry['prompt']['prompt_sha256'], 'answers': expected,
        'explanation': 'Synthetic prerequisite response.'}))
    ack(command('ADVANCE'))
    assignment = call('resume', {})['current_assignment']
    assert assignment['prompt']['exercise_id'] == spot
    answer = {'schema': 'spot-first-play-response-v2', 'exercise_id': spot,
        'prompt_sha256': assignment['prompt']['prompt_sha256'], 'first_play': first_play,
        'guaranteed_spot_tricks_from_position': count, 'explanation': 'My synthetic reasoning.'}
    receipt = command('RESPONSE', answer)
    assert receipt['result']['actual_mastery'] is None
    assert receipt['result']['criterion_met'] is None
    ack(receipt)
    terminal = command('ADVANCE')
    assert terminal['result']['status'] == 'AWAIT_TEACHER_REVIEW'
    assert terminal['result']['next_assignment'] is None
    assert request(candidate, journal, '/saved-answer')[0] == 409
    ack(terminal)
    status, saved = request(candidate, journal, '/saved-answer')
    assert status == 200
    saved = json.loads(saved)
    assert saved['saved_answer'] == {k: answer[k] for k in
        ['first_play', 'guaranteed_spot_tricks_from_position', 'explanation']}
    assert saved['next_assignment'] is None and saved['can_send_new_command'] is False
    assert json.loads(request(candidate, journal, '/saved-answer')[1]) == saved


def test_local_friendly_route_over_http(tmp_path):
    # Runs only on the Linux CI executor; no Windows socket diagnostic.
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    env = os.environ.copy()
    env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1')
    process = subprocess.Popen([sys.executable, '-m', 'site_integration.yan_spot_cycle.preview',
        '--output', str(tmp_path / 'preview'), '--port', str(port)], cwd=REPO, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        url = f'http://127.0.0.1:{port}/ru/practice/spot-cycle/'
        deadline = time.monotonic() + 15
        while True:
            try:
                with urllib.request.urlopen(url, timeout=3) as response:
                    page = response.read().decode('utf-8')
                    assert response.status == 200
                    break
            except urllib.error.URLError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        assert 'id="bridge-lesson-host"' in page
        asset = f'http://127.0.0.1:{port}/index.php?route=practice%2Fspot-cycle%2Fassets%2Flearner-presentation.json'
        with urllib.request.urlopen(asset, timeout=5) as response:
            assert len(json.load(response)['cards']) == 2
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
