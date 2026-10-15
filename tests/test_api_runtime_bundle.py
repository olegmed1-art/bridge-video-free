"""Exercise the actual staging route from deployable files, without a database.

Run directly for Docker COPY / Vercel-ignore composition checks, or with
``--smoke /app`` inside the real API image (only API dependencies required).
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib
import json
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
DATABASE_MODULES = (
    'video_staging_read', 'video_staging_lineage', 'video_candidate_persistence',
    'video_result_persistence', 'source_registration', 'source_identity_persistence',
    'runtime_worker_preflight',
)


def smoke(bundle: Path, runner: asyncio.Runner) -> dict:
    bundle = bundle.resolve()
    sys.path.insert(0, str(bundle))

    def forbidden(*args, **kwargs):
        raise AssertionError('NETWORK_OR_DATABASE_ACCESS_FORBIDDEN')

    socket.create_connection = forbidden
    socket.socket.connect = forbidden
    socket.socket.connect_ex = forbidden
    import psycopg
    psycopg.connect = forbidden
    for name in DATABASE_MODULES:
        module = importlib.import_module('database.' + name)
        assert Path(module.__file__).resolve().is_relative_to(bundle)
    assert sorted(p.name for p in (bundle / 'database').glob('*.py')) == sorted(
        name + '.py' for name in DATABASE_MODULES)
    assert not (bundle / 'database/migrations').exists()
    assert not (bundle / 'database/outbox_publisher.py').exists()
    assert not (bundle / 'universal_video').exists()

    from bridge_school_api import knowledge
    entry = importlib.import_module('app' if (bundle / 'app.py').exists() else 'bridge_school_api.main')
    calls = []
    queries = []

    class Cursor:
        def execute(self, sql, params=None):
            query = ' '.join(sql.split())
            expected = ('SHOW transaction_read_only', 'SHOW transaction_isolation',
                        'SELECT school_id FROM public.school', 'SELECT analysis_candidate_id,')
            assert len(queries) < len(expected), query
            assert query.startswith(expected[len(queries)]), query
            queries.append(query)

        def fetchone(self):
            return {1: ('on',), 2: ('repeatable read',), 4: None}[len(queries)]

        def fetchall(self):
            assert len(queries) == 3
            return [('00000000-0000-4000-8000-000000000002',)]

    class Connection:
        @contextmanager
        def transaction(self):
            assert self.read_only is True
            assert self.isolation_level == psycopg.IsolationLevel.REPEATABLE_READ
            yield

        @contextmanager
        def cursor(self, **kwargs):
            yield Cursor()

    @contextmanager
    def connect():
        calls.append('connect')
        yield Connection()

    knowledge.connect = connect
    os.environ['BRIDGE_API_TOKEN'] = 'synthetic-bundle-smoke'

    async def request(suffix, token=None):
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

        headers = [(b'host', b'test.invalid')]
        if token is not None:
            headers.append((b'authorization', ('Bearer ' + token).encode()))
        path = '/v1/knowledge/internal/video-staging/' + suffix
        scope = dict(type='http', asgi={'version': '3.0'}, http_version='1.1',
                     method='GET', scheme='http', path=path, raw_path=path.encode(),
                     root_path='', query_string=b'', headers=headers,
                     client=('127.0.0.1', 1234), server=('test.invalid', 80))
        await entry.app(scope, receive, send)
        start = next(m for m in messages if m['type'] == 'http.response.start')
        body = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        assert dict(start['headers'])[b'cache-control'] == b'private, no-store, max-age=0'
        return start['status'], json.loads(body)

    candidate = '00000000-0000-4000-8000-000000000001'
    assert runner.run(request(candidate))[0] == 401
    assert runner.run(request(candidate, 'wrong'))[0] == 403
    assert runner.run(request('invalid-uuid', 'synthetic-bundle-smoke'))[0] == 422
    assert calls == []
    status, result = runner.run(request(candidate, 'synthetic-bundle-smoke'))
    assert status == 200, result
    assert result['status'] == 'BLOCKED', result
    assert result['gaps'] == ['CANDIDATE_NOT_AVAILABLE'], result
    assert result['canon_write_allowed'] is False
    assert result['accepted_canon'] == [] and result['student_delivery_allowed'] is False
    assert calls == ['connect'] and len(queries) == 4
    return dict(status='PASS', entrypoint=entry.__name__, imports=len(DATABASE_MODULES),
                auth_statuses=[401, 403], invalid_uuid_status=422,
                missing_candidate='BLOCKED', actual_db_connections=0)


def tracked_files():
    raw = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT)
    return [p for p in raw.decode().split('\0') if p]


def copy_file(name, bundle, destination=None):
    target = (bundle / (destination or name)).resolve()
    assert target.is_relative_to(bundle.resolve())
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / name, target)


def compose_vercel(bundle):
    # .vercelignore uses gitignore rules; use Git's matcher, not a second parser.
    subprocess.run(['git', 'init', '-q', str(bundle)], check=True)
    shutil.copyfile(ROOT / '.vercelignore', bundle / '.gitignore')
    names = tracked_files()
    result = subprocess.run(['git', 'check-ignore', '--no-index', '-z', '--stdin'],
                            cwd=bundle, input=('\0'.join(names) + '\0').encode(), capture_output=True)
    assert result.returncode in (0, 1), result.stderr
    ignored = set(result.stdout.decode().split('\0'))
    for name in names:
        if name not in ignored:
            copy_file(name, bundle)


def compose_docker(bundle):
    text = (ROOT / 'Dockerfile.api').read_text().replace('\\\n', ' ')
    assert '\nWORKDIR /app\n' in text
    names = tracked_files()
    for line in text.splitlines():
        if not line.startswith('COPY '):
            continue
        args = shlex.split(line)[1:]
        assert len(args) >= 2 and all(not a.startswith(('-', '/')) for a in args)
        sources, destination = args[:-1], args[-1]
        for source in sources:
            if (ROOT / source).is_dir():
                for name in names:
                    if name.startswith(source + '/'):
                        copy_file(name, bundle, str(Path(destination) / Path(name).relative_to(source)))
            else:
                target = str(Path(destination) / Path(source).name) if destination.endswith('/') else destination
                copy_file(source, bundle, target)


def run_smoke(bundle):
    # Keep interpreter/platform essentials, never ambient service credentials.
    allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'LANG', 'LC_ALL'}
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    return subprocess.run([sys.executable, '-I', '-B', str(Path(__file__).resolve()), '--smoke', str(bundle)],
                          cwd=bundle, env=env, capture_output=True, text=True, timeout=30)


class RuntimeBundleTests(unittest.TestCase):
    def check_bundle(self, compose):
        with tempfile.TemporaryDirectory(prefix='bridge-api-bundle-') as temporary:
            bundle = Path(temporary).resolve()
            self.assertEqual(bundle.parent, Path(tempfile.gettempdir()).resolve())
            compose(bundle)
            result = run_smoke(bundle)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'PASS')

    def test_vercel_bundle_route(self):
        self.check_bundle(compose_vercel)

    def test_docker_copy_bundle_route(self):
        self.check_bundle(compose_docker)

    def test_missing_reader_is_detected(self):
        with tempfile.TemporaryDirectory(prefix='bridge-api-bundle-') as temporary:
            bundle = Path(temporary).resolve()
            self.assertEqual(bundle.parent, Path(tempfile.gettempdir()).resolve())
            compose_docker(bundle)
            (bundle / 'database/video_staging_read.py').unlink()
            result = run_smoke(bundle)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No module named 'database.video_staging_read'", result.stderr)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--smoke':
        # Windows creates the event loop's internal socketpair at initialization.
        # Finish that before forbidding application networking; reuse one loop.
        with asyncio.Runner() as runner:
            runner.get_loop()
            print(json.dumps(smoke(Path(sys.argv[2]), runner)))
    else:
        unittest.main()
