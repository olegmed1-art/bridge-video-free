"""Bounded Linux PR smoke of the real source-tree API; no DB or network.

Runs only in the existing nonroot API image with network disabled and a read-only
source mount. Raw ASGI messages are observations, never mocked handler results.
This does not qualify image packaging, working DB access, or production release.
"""
from __future__ import annotations

import argparse
import asyncio
import faulthandler
from collections import Counter
import hashlib
import importlib
import json
import os
import re
import signal
from pathlib import Path
import socket
import sys
import threading
from urllib.parse import urlencode, parse_qsl

REPO = Path(__file__).resolve().parents[1]
HEAD = REVISION = TREE = IMAGE = None
ALGORITHM_SOURCE_HEAD = 'c16ce47b2b653909ef6fbb5d8d17848ae7a2da28'
PROFILE = 'SCHOOL_L1_DB_V1'
TOKEN = 'github-actions-smoke-token'  # Existing CI's synthetic local-only value.
FINGERPRINT = '143d7387734a721ebedd6cab78af21278f7b250d963ee2c82562e50dc3bbfd33'
PIN_PATHS = (
    'app.py', 'bridge_school_api/__init__.py', 'bridge_school_api/main.py',
    'bridge_school_api/knowledge.py', 'bridge_school_api/db.py',
    'bridge_school_api/l1_canonical_registry.py',
    'bridge_school_api/l1_canonical_runtime.py', 'bridge_school_api/l1_canonical_runtime_v2.py',
    'bridge_school_api/l1_canonical_runtime_v3.py', 'bridge_school_api/l1_canonical_runtime_v4.py',
    'tools/l1_ci_smoke_scope.py', 'tools/api_smoke_session.py',
    'bridge_school_api/incident_db_probe.py', 'requirements-api.txt',
    '.github/workflows/bridge-school-api-ci.yml', 'vercel.json',
)


# Exact unchanged API/algorithm source; workflow and driver are recorded at launch.
EXPECTED_SOURCE_PINS = {
    "app.py": "53ae2bc33c456a3902a2776bfb3d99557e1c0033dd0741e5e8c17139fc44ae27",
    "bridge_school_api/__init__.py": "d8d1edecff6e8a3b1eda3c3ecd22fa80ce9708a3ce41fffecc80aca48b12ee32",
    "bridge_school_api/main.py": "a0dcbe8890a4cce33ca82f9e504d8ae9d93e7aa41290f1059b428f4ce5c35d25",
    "bridge_school_api/knowledge.py": "52b4c6c57b2fcf7005401858e4a6d369409e2cc49335ac9523a294d4e5bf222d",
    "bridge_school_api/db.py": "1c391cd0bfde393994db6b3b2f71af7843f5ed48b79e5476eced02113338234a",
    "bridge_school_api/l1_canonical_registry.py": "4f724aa0b4c350b1ed36b77c5d39da5a217c9b17e7369a29201a3fe4fa61679b",
    "bridge_school_api/l1_canonical_runtime.py": "fb2a6b5e9f98525edda370edd33bb14c0ae1f0b6650e7e2110023eb8016e2a25",
    "bridge_school_api/l1_canonical_runtime_v2.py": "9b4bc446e1bed8bc578cbfcc16200915a4c1d2127734f345e06b32b7e10c2001",
    "bridge_school_api/l1_canonical_runtime_v3.py": "ea9e2d80de9c5187827e918a5e91d2441da70cf4042f91f42afeea673e11d443",
    "bridge_school_api/l1_canonical_runtime_v4.py": "cff18e792d934f5a1a2442c2450c78adce6fb92dd1e79916330840e37a33eee5",
    "tools/l1_ci_smoke_scope.py": "21f18e5b6f7022cd641e956774b072e38eb1632ccf976b8e9f9ce0eda53e8123",
    "tools/api_smoke_session.py": "fbea18f32fe041ba7b925b518d772513a71056daf0885f6d162a01da298858a4",
    "bridge_school_api/incident_db_probe.py": "d229f0441b206a9496d94fa20b378856524ce4019032d8bbc4672e2c9c5b6ab5",
    "requirements-api.txt": "58bc03f9ee798e8dc05e064aeca451107c9cf115a130440ae4676d9f9c3472d4",
    "vercel.json": "09ee407ca9dd3c050c982116fe3a62225d2c2675e955d65d02e61ce3877a15f0"
}


def pins():
    return {name: hashlib.sha256((REPO / name).read_bytes()).hexdigest() for name in PIN_PATHS}


class RawResponse:
    """Decode only actual ASGI output; no handler, auth or database override."""
    def __init__(self, messages):
        assert isinstance(messages, list) and len(messages) >= 2
        start, bodies = messages[0], messages[1:]
        assert start['type'] == 'http.response.start'
        assert type(start['status']) is int and 100 <= start['status'] <= 599
        assert all(message['type'] == 'http.response.body' for message in bodies)
        assert all(type(message.get('body', b'')) is bytes for message in bodies)
        assert all(message.get('more_body', False) is True for message in bodies[:-1])
        assert bodies[-1].get('more_body', False) is False
        self.status_code = start['status']
        self.headers = {key.decode('latin1').lower(): value.decode('latin1')
                        for key, value in start['headers']}
        self.body = b''.join(message.get('body', b'') for message in bodies)

    def json(self):
        return json.loads(self.body)


class RawASGIClient:
    def __init__(self, app):
        self.app = app

    async def __aenter__(self):
        return self

    async def __aexit__(self, *unused):
        return False

    async def request(self, method, path, params, headers):
        messages = []
        received = False

        async def receive():
            nonlocal received
            if not received:
                received = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await asyncio.Event().wait()

        async def send(message):
            messages.append(message)

        wire_headers = [(b'host', b'smoke.invalid')]
        wire_headers += [(key.lower().encode('ascii'), value.encode('ascii'))
                         for key, value in headers.items()]
        scope = dict(type='http', asgi={'version': '3.0'}, http_version='1.1',
                     method=method, scheme='http', path=path, raw_path=path.encode(),
                     root_path='', query_string=urlencode(params).encode(),
                     headers=wire_headers, client=('127.0.0.1', 1234),
                     server=('smoke.invalid', 80))
        await self.app(scope, receive, send)
        return RawResponse(messages)


def verify_source():
    observed = pins()
    for name, expected in EXPECTED_SOURCE_PINS.items():
        assert observed[name] == expected, 'source pin:' + name
    return observed


def main():
    assert sys.platform == 'linux', 'runtime smoke is Linux-only; no Windows retry'
    assert hasattr(os, 'getuid') and os.getuid() == os.getgid() == 65532
    faulthandler.dump_traceback_later(15)
    def progress(phase_name):
        print(json.dumps({'progress': phase_name}), file=sys.stderr, flush=True)
    progress('child_started')
    assert not any(key.startswith('PG') or key.endswith('DATABASE_URL') for key in os.environ)
    assert os.environ.get('BRIDGE_API_TOKEN') == TOKEN
    assert 'VERCEL_ENV' not in os.environ and 'VERCEL_OIDC_TOKEN' not in os.environ
    before = verify_source()
    driver_before = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    sys.path.insert(0, str(REPO))
    boundaries = {'self_test': Counter(), 'http_smoke': Counter()}
    phase = 'self_test'

    def denied(kind):
        boundaries[phase][kind] += 1
        raise RuntimeError('LOCAL_SMOKE_FORBIDDEN_' + kind.upper())

    # An asyncio control channel is initialized before denying application I/O.
    # No HTTP listener, external target, or production process is started.
    with asyncio.Runner() as runner:
        runner.get_loop()
        progress('event_loop_ready')

        def audit(event, args):
            if event in {'socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname',
                         'socket.gethostbyaddr', 'socket.sendto', 'socket.bind'}:
                denied('network')
            if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec'}:
                denied('process_launch')
            if event == 'open':
                mode, flags = args[1], args[2]
                if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                    denied('file_write')

        sys.addaudithook(audit)
        progress('audit_guards_ready')
        import psycopg
        progress('psycopg_imported')

        def no_database(*args, **kwargs):
            denied('database')

        psycopg.connect = no_database
        psycopg.Connection.connect = no_database
        psycopg.AsyncConnection.connect = no_database

        # Negative controls exercise real entry functions before a syscall can run.
        with socket.socket() as control:
            try:
                control.connect(('203.0.113.1', 9))
            except RuntimeError as exc:
                assert str(exc) == 'LOCAL_SMOKE_FORBIDDEN_NETWORK'
            else:
                raise AssertionError('network guard did not reject the control')
        try:
            psycopg.connect('synthetic-not-a-dsn')
        except RuntimeError as exc:
            assert str(exc) == 'LOCAL_SMOKE_FORBIDDEN_DATABASE'
        else:
            raise AssertionError('DB guard did not reject the control')
        assert boundaries['self_test'] == {'network': 1, 'database': 1}
        phase = 'http_smoke'
        progress('guard_self_tests_passed')

        import fastapi
        import starlette
        progress('http_dependencies_imported')
        entry = importlib.import_module('app')
        progress('actual_app_imported')
        from bridge_school_api import knowledge
        apis = [importlib.import_module('bridge_school_api.' + name) for name in (
            'l1_canonical_runtime', 'l1_canonical_runtime_v2',
            'l1_canonical_runtime_v3', 'l1_canonical_runtime_v4')]
        assert Path(entry.__file__).resolve() == REPO / 'app.py'
        assert Path(knowledge.__file__).resolve() == REPO / 'bridge_school_api/knowledge.py'
        for api in apis:
            assert Path(api.__file__).resolve() == REPO / ('bridge_school_api/' + api.__name__.split('.')[-1] + '.py')
        assert not entry.app.dependency_overrides
        assert knowledge.evaluate is apis[3].evaluate
        assert not os.environ.get('BRIDGE_APP_DATABASE_URL')
        assert fastapi.__version__ == '0.141.1' and psycopg.__version__ == '3.3.4'
        profile_counts = Counter()
        codes = {api.evaluate.__code__: name for api, name in zip(apis, ('v1', 'v2', 'v3', 'v4'))}

        def observe(frame, event, arg):
            if event == 'call' and frame.f_code in codes:
                profile_counts[codes[frame.f_code]] += 1

        sys.setprofile(observe)
        threading.setprofile(observe)
        observations = []
        catalog_body = None

        async def smoke():
            nonlocal catalog_body
            async with RawASGIClient(entry.app) as client:
                async def request(case_id, method, path, params, auth, expected):
                    progress('request_start:' + case_id)
                    assert len(observations) < 25
                    prefix = 'bearer ' if auth == 'lowercase' else 'Bearer '
                    headers = {} if auth == 'missing' else {'Authorization': prefix + (TOKEN if auth in {'valid', 'lowercase'} else 'wrong')}
                    response = await asyncio.wait_for(client.request(method, path, params=params, headers=headers), timeout=3)
                    body = response.json()
                    record = {'id': case_id, 'method': method, 'path': path, 'params': params,
                              'auth': auth, 'request_headers': dict(headers),
                              'token_configured': bool(os.environ.get('BRIDGE_API_TOKEN')),
                              'request': {'method': method, 'path': path,
                                          'query': dict(parse_qsl(urlencode(params), keep_blank_values=True)),
                                          'authorization': headers.get('Authorization'),
                                          'token_configured': bool(os.environ.get('BRIDGE_API_TOKEN'))},
                              'expected_status': expected, 'status': response.status_code,
                              'headers': dict(response.headers), 'body': body}
                    observations.append(record)
                    progress('request_end:' + case_id + ':' + str(response.status_code))
                    assert response.status_code == expected, case_id
                    assert response.headers['x-content-type-options'] == 'nosniff'
                    assert response.headers['referrer-policy'] == 'no-referrer'
                    if path.startswith('/v1/'):
                        assert response.headers['cache-control'] == 'private, no-store, max-age=0'
                        assert response.headers['pragma'] == 'no-cache'
                    return body

                path = '/v1/knowledge/runtime/l1'
                params = {'system_profile': PROFILE}
                await request('auth_missing', 'GET', path, params, 'missing', 401)
                await request('auth_wrong', 'GET', path, params, 'wrong', 403)
                await request('auth_case', 'GET', path, params, 'lowercase', 401)
                await request('profile_missing', 'GET', path, {}, 'valid', 422)
                await request('profile_empty', 'GET', path, {'system_profile': ''}, 'valid', 422)
                await request('profile_blank', 'GET', path, {'system_profile': '   '}, 'valid', 422)
                catalog_body = await request('catalog', 'GET', path, params, 'valid', 200)
                assert catalog_body['engine_version'] == 'l1-canonical-runtime-v4-observation-safe-v1'
                assert catalog_body['system_profile'] == PROFILE
                assert catalog_body['rule_id_fingerprint'] == FINGERPRINT
                assert catalog_body['lifecycle'] == 'RUNTIME_SNAPSHOT'
                assert catalog_body['formal_db_activation_asserted'] is False
                assert catalog_body['fallback_performed'] is False
                assert catalog_body['count'] == 111 and len(catalog_body['rules']) == 111
                assert len({r['stable_key'] for r in catalog_body['rules']}) == 111
                assert [r['stable_key'] for r in catalog_body['rules'] if not r['executable']] == ['RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE']
                rule = await request('focused_rule', 'GET', path, {**params, 'rule_id': 'RULE-L1-PLAY-NT-COUNT-TOP-TRICKS'}, 'valid', 200)
                assert rule['rules'] == [{'stable_key': 'RULE-L1-PLAY-NT-COUNT-TOP-TRICKS', 'executable': True}]
                assert rule['formal_db_activation_asserted'] is False
                unbounded = await request('catalog_unbounded', 'GET', path, {**params, 'rule_id': 'RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE'}, 'valid', 200)
                assert unbounded['rules'] == [{'stable_key': 'RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE', 'executable': False}]
                gap = await request('unknown_rule', 'GET', path, {**params, 'rule_id': 'SYNTHETIC_UNKNOWN_RULE'}, 'valid', 200)
                assert gap['retrieval_status'] == 'RUNTIME_GAP' and gap['count'] == 0 and gap['rules'] == []
                tour = await request('catalog_tournament_rule', 'GET', path, {**params, 'rule_id': 'RULE-TOUR-OPEN-1NT'}, 'valid', 200)
                assert tour['retrieval_status'] == 'RUNTIME_GAP' and tour['count'] == 0 and tour['rules'] == []
                end = await request('catalog_offset_end', 'GET', path, {**params, 'offset': 111}, 'valid', 200)
                assert end['retrieval_status'] == 'RUNTIME_GAP' and end['count'] == 0 and end['rules'] == []
                other = await request('wrong_profile', 'GET', path, {'system_profile': 'SCHOOL_TOURNAMENT_CURRENT_V1'}, 'valid', 404)
                assert other['detail']['code'] == 'RUNTIME_PROFILE_GAP'
                world = await request('profile_world', 'GET', path, {'system_profile': 'WORLD_EXTERNAL'}, 'valid', 404)
                assert world['detail']['code'] == 'RUNTIME_PROFILE_GAP'
                await request('limit_low', 'GET', path, {**params, 'limit': 0}, 'valid', 422)
                await request('limit_out_of_range', 'GET', path, {**params, 'limit': 501}, 'valid', 422)
                await request('offset_out_of_range', 'GET', path, {**params, 'offset': -1}, 'valid', 422)
                await request('rule_empty', 'GET', path, {**params, 'rule_id': ''}, 'valid', 422)
                await request('method_not_allowed', 'POST', path, params, 'valid', 405)
                await request('overview_auth_missing', 'GET', '/v1/overview', {}, 'missing', 401)
                await request('overview_auth_wrong', 'GET', '/v1/overview', {}, 'wrong', 403)
                for case_id, db_path in [('overview_no_dsn', '/v1/overview'), ('health_no_dsn', '/healthz')]:
                    body = await request(case_id, 'GET', db_path, {}, 'valid', 503)
                    assert body == {'detail': 'service unavailable'}
                saved = os.environ.pop('BRIDGE_API_TOKEN')
                try:
                    await request('server_token_unconfigured', 'GET', path, params, 'valid', 503)
                finally:
                    os.environ['BRIDGE_API_TOKEN'] = saved
                after = await request('catalog_after_errors', 'GET', path, params, 'valid', 200)
                assert after == catalog_body

        runner.run(smoke())
        progress('requests_finished')
        sys.setprofile(None)
        threading.setprofile(None)
        assert len(observations) == 25
        assert not boundaries['http_smoke'], dict(boundaries['http_smoke'])
        assert all(profile_counts[version] > 0 for version in ('v1', 'v2', 'v3', 'v4'))
        assert not entry.app.dependency_overrides
        after = verify_source()
        assert driver_before == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        assert before == after
        result = {'schema': 'l1-linux-source-api-smoke-v1', 'status': 'PASS_LOCAL_API_ONLY',
                  'head': HEAD, 'revision': REVISION, 'tree': TREE, 'image': IMAGE,
                  'algorithm_source_head': ALGORITHM_SOURCE_HEAD, 'driver_sha256': driver_before,
                  'source_pins_before': before, 'source_pins_after': after,
                  'observations': observations, 'public_evaluate_profile_counts': dict(profile_counts),
                  'guard_counters': {key: dict(value) for key, value in boundaries.items()},
                  'dependency_overrides': 0, 'fake_successful_db_responses': 0,
                  'dependencies': {'fastapi': fastapi.__version__, 'psycopg': psycopg.__version__,
                                   'starlette': starlette.__version__, 'python': sys.version},
                  'database_dsn_present': False, 'actual_db_connections': 0,
                  'http_listener_started': False, 'request_budget': 25, 'per_request_timeout_seconds': 3,
                  'isolation': 'nonroot readonly container, --network none, raw ASGI; Python network/DB/process/write denial',
                  'packaging_qualified': False, 'deployed_entrypoint_qualified': False,
                  'production_smoke_qualified': False, 'old_exemption_applied': False,
                  'working_db_positive_qualified': False, 'production_credentials_used': False,
                  'release_hold': True, 'full_call_expanded': False}
        encoded = json.dumps(result, ensure_ascii=True)
        assert len(encoded.encode()) <= 256 * 1024
        print(encoded, flush=True)
        faulthandler.cancel_dump_traceback_later()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--smoke', type=Path, required=True)
    parser.add_argument('--head', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--tree', required=True)
    parser.add_argument('--image', required=True)
    parser.add_argument('--check-source', action='store_true')
    args = parser.parse_args()
    REPO = args.smoke.resolve()
    HEAD, REVISION, TREE, IMAGE = args.head, args.revision, args.tree, args.image
    assert all(re.fullmatch(r'[0-9a-f]{40}', value) for value in (HEAD, REVISION, TREE))
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', IMAGE)
    launch = {'head': HEAD, 'revision': REVISION, 'tree': TREE, 'image': IMAGE,
              'driver_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'source_pins': verify_source()}
    print(json.dumps({'launch': launch}), file=sys.stderr, flush=True)
    if args.check_source:
        print(json.dumps({'status': 'PASS_SOURCE_CHECK_ONLY', **launch}))
    else:
        def expired(signum, frame):
            raise TimeoutError('LOCAL_SMOKE_GLOBAL_WALL_45_SECONDS')
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(45)
        try:
            main()
        finally:
            signal.alarm(0)
            faulthandler.cancel_dump_traceback_later()
