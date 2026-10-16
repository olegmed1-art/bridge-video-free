"""Linux-only real PHP -> HTTP backend -> persistent journal/restart check.

Creates explicitly new automated responses. Never imports historical learner work.
Output lives in a named deliverables directory, not pytest's temporary fixture.
Only artifact/ is uploaded; private/runtime.json (CSRF nonce) is excluded.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from bridge_school_api.local_spot_session import load_store_class
from .build_candidate import HERE, REPO
from .create_local import create


def stop(child):
    if child is not None and child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def ports():
    # Keep both reservations open until we have two distinct ports.
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        return first.getsockname()[1], second.getsockname()[1]


def verify(output: Path):
    if sys.platform != 'linux':
        raise RuntimeError('HTTP verification runs only in the authorized Linux CI')
    frontend_port, backend_port = ports()
    frontend, backend = f'http://127.0.0.1:{frontend_port}', f'http://127.0.0.1:{backend_port}'
    create(output, frontend, backend)
    private = output/'private'
    bindings = json.loads((private/'bindings.json').read_bytes())
    assignments = bindings['assignments']
    env = os.environ.copy()
    env.update(PYTHONPATH=str(REPO), PYTHONDONTWRITEBYTECODE='1',
        BRIDGE_LOCAL_SINGLE_USER='1', BRIDGE_YAN_CANDIDATE=str(output/'site'),
        BRIDGE_SINGLE_USER_FRONTEND_ORIGIN=frontend,
        BRIDGE_SINGLE_USER_BACKEND_ORIGIN=backend)
    backend_command = [sys.executable, '-m', 'site_integration.yan_spot_cycle.local_backend',
                       '--private-dir', str(private)]
    def start_backend():
        return subprocess.Popen(backend_command, cwd=REPO, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    def url(activity, suffix=''):
        return frontend+'/index.php?'+urllib.parse.urlencode({
            'route': 'practice/spot-cycle'+suffix, 'activity': activity})
    def http(target, *, message=None, headers=None):
        payload = json.dumps(message).encode() if message is not None else None
        request = urllib.request.Request(target, data=payload, headers=headers or {})
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                return response.status, response.read().decode('utf-8')
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode('utf-8')
    def ready(child, target):
        deadline = time.monotonic()+20
        while time.monotonic() < deadline and child.poll() is None:
            try:
                status, body = http(target)
                if status == 200:
                    return body
            except urllib.error.URLError:
                pass
            time.sleep(0.1)
        raise AssertionError('real local HTTP server did not become ready')
    def state(activity):
        record = assignments[activity]
        return load_store_class()(private/record['journal'], record['session_id']).resume()
    def hashes():
        return {activity: hashlib.sha256((private/record['journal']).read_bytes()).hexdigest()
                for activity, record in assignments.items()}
    backend_process = frontend_process = None
    saved_answers = {}
    denials = 0
    try:
        backend_process = start_backend()
        frontend_process = subprocess.Popen(['php', '-S', f'127.0.0.1:{frontend_port}',
            str(HERE/'local_router.php')], cwd=REPO, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        page = ready(backend_process, frontend+'/ru/practice/spot-cycle/')
        assert 'bridge-lesson-host' in page and 'bridge-local-assigned-activities' in page
        assert all('activity='+activity in page for activity in assignments)
        before_denials = hashes()
        for target, message, headers in [
            (url('spot.a7c2'), None, {'X-Learner-Id': 'other'}),
            (url('spot.a7c2'), None, {'X-School-Id': 'other'}),
            (url('unknown'), None, {}),
            (url('spot.a7c2', '/transport'), {'operation': 'resume', 'value': {}}, {}),
            (url('spot.a7c2', '/transport'), {'operation': 'resume', 'value': {}},
             {'Origin': frontend, 'X-CSRF-Token': 'wrong'}),
            (url('spot.a7c2'), None, {'Origin': 'http://evil.invalid'}),
            (url('spot.a7c2'), None, {'X-Forwarded-For': '127.0.0.1'}),
        ]:
            assert http(target, message=message, headers=headers)[0] == 403
            denials += 1
        assert hashes() == before_denials
        for activity, first_play, count in [('spot.a7c2', 'DA', 1), ('spot.f3b8', 'HA', 0)]:
            status, page = http(url(activity))
            assert status == 200
            token = re.search(r'<meta name="bridge-school-csrf-token" content="([^"]+)">', page)[1]
            config = json.loads(re.search(r'<script type="application/json" id="bridge-lesson-host">(.*?)</script>', page)[1])
            assert all(urllib.parse.parse_qs(urllib.parse.urlsplit(endpoint).query)['activity'] == [activity]
                       for endpoint in config.values())
            public_status, public = http(url(activity, '/assets/learner-presentation.json'))
            assert public_status == 200 and len(json.loads(public)['cards']) == 2
            assert not any(key in public for key in ('expected_solution', 'teacher_version', 'answer_key'))
            assert http(url(activity, '/assets/lesson.teacher.json'))[0] == 404
            headers = {'Origin': frontend, 'X-CSRF-Token': token, 'Content-Type': 'application/json'}
            def call(operation, value):
                status, body = http(url(activity, '/transport'), headers=headers,
                                    message={'operation': operation, 'value': value})
                assert status == 200, f'{operation} rejected: HTTP {status}'
                return json.loads(body)
            def command(kind, payload=None):
                current = call('resume', {})
                return call('execute', {'command': {'command_id': f"new-ci-{kind}-{current['revision']}",
                    'kind': kind, 'issuance_id': current['current_assignment']['issuance_id'],
                    'payload': payload}, 'expected_revision': current['revision']})
            def ack(delivery):
                return call('acknowledge', {k: delivery[k] for k in ('delivery_id', 'result_sha256')})
            initial = call('resume', {})
            assert initial['revision'] == 1
            entry = initial['current_assignment']
            assert entry['prompt']['exercise_id'] == 'entry.e1'
            # Explicit NEW automated prerequisite input, never historical work.
            step = importlib.import_module('_local_spot_candidate.teaching_step')
            expected = step.exercise_version_fields('entry.e1')['exercise_version']['expected_solution']['expected_answers']
            ack(command('RESPONSE', {'schema': 'low-card-structured-response-v1',
                'exercise_id': 'entry.e1', 'prompt_sha256': entry['prompt']['prompt_sha256'],
                'answers': expected, 'explanation': 'NEW automated verification prerequisite.'}))
            ack(command('ADVANCE'))
            spot = call('resume', {})['current_assignment']
            assert spot['prompt']['exercise_id'] == activity
            answer = {'schema': 'spot-first-play-response-v2', 'exercise_id': activity,
                'prompt_sha256': spot['prompt']['prompt_sha256'], 'first_play': first_play,
                'guaranteed_spot_tricks_from_position': count,
                'explanation': 'NEW automated verification answer, awaiting teacher review.'}
            receipt = command('RESPONSE', answer)
            assert receipt['result']['actual_mastery'] is None and receipt['result']['criterion_met'] is None
            ack(receipt)
            terminal = command('ADVANCE')
            assert terminal['result']['status'] == 'AWAIT_TEACHER_REVIEW'
            assert terminal['result']['next_assignment'] is None
            assert http(url(activity, '/saved-answer'))[0] == 409
            ack(terminal)
            status, body = http(url(activity, '/saved-answer'))
            assert status == 200
            saved = json.loads(body)
            assert saved['saved_answer'] == {key: answer[key] for key in (
                'first_play', 'guaranteed_spot_tricks_from_position', 'explanation')}
            assert saved['next_assignment'] is None and saved['can_send_new_command'] is False
            assert state(activity)['revision'] == 9
            saved_answers[activity] = saved
        committed_hashes = hashes()
        old_pid = backend_process.pid
        stop(backend_process)
        backend_process = start_backend()
        assert backend_process.pid != old_pid
        ready(backend_process, url('spot.a7c2'))
        for activity, expected_saved in saved_answers.items():
            status, body = http(url(activity, '/saved-answer'))
            assert status == 200 and json.loads(body) == expected_saved
            assert state(activity)['revision'] == 9
        assert hashes() == committed_hashes
    finally:
        stop(frontend_process)
        stop(backend_process)
    artifact = output/'artifact'
    (artifact/'journals').mkdir(parents=True)
    for activity, record in assignments.items():
        shutil.copyfile(private/record['journal'], artifact/record['journal'])
        # Validate the copies by normal replay, without rewriting any records.
        assert load_store_class()(artifact/record['journal'], record['session_id']).resume()['revision'] == 9
        with sqlite3.connect((artifact/record['journal']).as_uri()+'?mode=ro', uri=True) as db:
            assert db.execute('SELECT COUNT(*) FROM journal').fetchone()[0] == 9
    shutil.copyfile(private/'bindings.json', artifact/'bindings.json')
    report = {'schema': 'yan-persistent-local-verification-v1', 'verified_utc': datetime.now(timezone.utc).isoformat(),
        'mode': 'LOCAL_SINGLE_USER', 'website_visitor_authentication': False,
        'actor': 'AUTOMATED_VERIFICATION_NEW_RECORDS', 'historical_work_imported': False,
        'new_target_responses': 2, 'new_prerequisite_responses': 2, 'denied_requests': denials,
        'backend_restarted': True, 'saved_answers_identical_after_restart': True,
        'journal_hashes_unchanged_after_restart_reads': True, 'journal_sha256': committed_hashes,
        'journal_revisions': {activity: 9 for activity in assignments},
        'terminal_status': 'AWAIT_TEACHER_REVIEW', 'next_assignment': None,
        'artifact_omits_runtime_csrf_nonce': True, 'production_without_resolver': 'HTTP_503'}
    (artifact/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    (artifact/'teacher-review-queue.json').write_text(json.dumps(saved_answers, indent=2)+'\n', encoding='utf-8')
    (artifact/'README.txt').write_text('New automated verification records only. No historical learner work.\n'
        'Two replay-validated journals with pending manual teacher review.\n'
        'runtime.json and its CSRF nonce are deliberately excluded.\n', encoding='utf-8')
    assert sorted(p.relative_to(artifact).as_posix() for p in artifact.rglob('*') if p.is_file()) == [
        'README.txt', 'bindings.json', 'journals/spot.a7c2.sqlite3', 'journals/spot.f3b8.sqlite3',
        'report.json', 'teacher-review-queue.json']
    print(json.dumps(report, sort_keys=True))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    verify(args.output.resolve())
