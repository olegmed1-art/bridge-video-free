"""Six actual packaged-ASGI checks. Importing this module runs no application.

Runtime is Linux/cgroup-v2 only and requires a separately admitted local image.
Offline unit fixtures qualify the verifier, never the packaged application.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import re
import signal
import socket
import sys
import threading
from urllib.parse import urlencode

SPEC_SHA = '2ca9c696b027134558788aec64105de3398d70e73ee56c6e4442ee41cfdcfa70'
SOURCE = '2eec5ba690057463e0c5e89b2a429f46521c640c'
TREE = 'fcc4da1306000c103585269a22dfc6184d348a1c'
TOKEN = 'packaged-local-only-synthetic'
SNAPSHOT_SOURCE = 'Drive catalog / AI \u2014 \u041f\u0440\u0430\u0432\u0438\u043b\u0430 \u0441\u0438\u0441\u0442\u0435\u043c\u044b'
ROOT = Path('/app')
UNBOUNDED = 'RULE-L1-COMPETITIVE-STRENGTH-PRINCIPLE'
PUBLIC_FIELDS = {'contract_version', 'authority_lane', 'lifecycle',
    'formal_db_activation_asserted', 'system_profile', 'engine_version',
    'snapshot_source', 'snapshot_date', 'rule_id_fingerprint',
    'retrieval_status', 'fallback_performed', 'count', 'rules'}


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'JSON_DUPLICATE_KEY')
            result[key] = value
        return result
    def bad_number(_):
        raise Rejected('JSON_NONFINITE')
    def finite_float(value):
        result = float(value)
        require(math.isfinite(result), 'JSON_NONFINITE')
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=bad_number, parse_float=finite_float)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Rejected('JSON_INVALID') from exc


def load_spec(path):
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == SPEC_SHA, 'SPEC_PIN_MISMATCH')
    result = strict_json(raw)
    require(result['proposed_source_commit'] == SOURCE and len(result['cases']) == 6,
            'SPEC_SCOPE')
    return result


def verify_files(root, expected):
    require(root.resolve() == root and not root.is_symlink(), 'PACKAGED_ROOT')
    result = {}
    for name, digest in expected.items():
        file = root / name
        require(not file.is_symlink() and file.resolve().is_relative_to(root), 'FILE_ESCAPE')
        raw = file.read_bytes()
        require(len(raw) <= 512 * 1024, 'FILE_SIZE')
        actual = hashlib.sha256(raw).hexdigest()
        require(actual == digest, 'FILE_PIN_MISMATCH')
        result[name] = actual
    return result


def verify_cgroups(values):
    require(set(values) == {'memory.max', 'memory.swap.max', 'pids.max', 'cpu.max'}, 'CGROUP_FIELDS')
    require(values['memory.max'].strip() == '536870912', 'CGROUP_MEMORY')
    require(values['memory.swap.max'].strip() == '0', 'CGROUP_SWAP')
    require(values['pids.max'].strip() == '64', 'CGROUP_PIDS')
    cpu = values['cpu.max'].split()
    require(len(cpu) == 2 and all(re.fullmatch('[0-9]+', x) for x in cpu), 'CGROUP_CPU')
    quota, period = map(int, cpu)
    require(quota > 0 and quota == period, 'CGROUP_CPU')
    return {key: value.strip() for key, value in values.items()}


def verify_mounts(text):
    roots = []
    inputs = set()
    for line in text.splitlines():
        fields = line.split()
        require(len(fields) >= 10 and '-' in fields, 'MOUNTINFO_INVALID')
        point, options = fields[4], set(fields[5].split(','))
        require(not point.startswith('/source') and not (point == '/app' or point.startswith('/app/')),
                'SOURCE_OR_APP_OVERLAY')
        if point == '/':
            roots.append(options)
        if point.startswith('/qualification/'):
            require(point in {'/qualification/driver.py', '/qualification/spec.json'} and 'ro' in options,
                    'INPUT_MOUNT')
            inputs.add(point)
    require(len(roots) == 1 and 'ro' in roots[0], 'ROOTFS_WRITABLE')
    require(inputs == {'/qualification/driver.py', '/qualification/spec.json'}, 'INPUT_MOUNTS_MISSING')


def verify_environment(env):
    require(env.get('BRIDGE_API_TOKEN') == TOKEN, 'SYNTHETIC_TOKEN_REQUIRED')
    forbidden = [key for key in env if key.startswith('PG') or key.endswith('DATABASE_URL')
        or 'PROXY' in key.upper() or key.startswith('VERCEL')
        or any(word in key.upper() for word in ('PASSWORD', 'SECRET', 'CREDENTIAL'))
        or ('TOKEN' in key.upper() and key != 'BRIDGE_API_TOKEN')]
    require(not forbidden, 'ENVIRONMENT_REQUIRES_REVIEW')


def platform_preflight():
    require(sys.platform == 'linux' and platform.machine() == 'x86_64', 'LINUX_AMD64_REQUIRED')
    require(os.getuid() == os.getgid() == 65532, 'NONROOT_IDENTITY')
    require(not Path('/source').exists(), 'SOURCE_PRESENT')
    require(Path('/proc/self/cgroup').read_text().strip() == '0::/', 'PRIVATE_CGROUP_V2_REQUIRED')
    base = Path('/sys/fs/cgroup')
    groups = verify_cgroups({key: (base / key).read_text() for key in
        ('memory.max', 'memory.swap.max', 'pids.max', 'cpu.max')})
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    require(status['NoNewPrivs'].strip() == '1', 'NO_NEW_PRIVILEGES')
    require(int(status['CapEff'], 16) == int(status['CapBnd'], 16) == 0, 'CAPABILITIES')
    require({p.name for p in Path('/sys/class/net').iterdir()} == {'lo'}, 'NETWORK_INTERFACE')
    verify_mounts(Path('/proc/self/mountinfo').read_text())
    verify_environment(os.environ)
    return groups


class IOAudit:
    def __init__(self):
        self.phase = 'controls'
        self.counts = {'controls': Counter(), 'application': Counter()}

    def deny(self, kind):
        self.counts[self.phase][kind] += 1
        raise Rejected('FORBIDDEN_' + kind.upper())

    def __call__(self, event, args):
        if event in {'socket.connect', 'socket.bind', 'socket.getaddrinfo',
                     'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.sendto'}:
            self.deny('network')
        if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn', 'os.exec', 'os.fork'}:
            self.deny('process')
        if event in {'os.remove', 'os.rename', 'os.rmdir', 'os.mkdir', 'os.chmod',
                     'os.chown', 'os.link', 'os.symlink', 'os.truncate', 'os.utime'}:
            self.deny('write')
        if event == 'open':
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                self.deny('write')


class ResponseStream:
    """Bounded HTTP response automaton; never manufactures handler results."""
    def __init__(self):
        self.status = None
        self.headers = None
        self.body = bytearray()
        self.finished = False
        self.messages = 0

    def feed(self, message):
        self.messages += 1
        require(self.messages <= 64 and type(message) is dict and not self.finished, 'ASGI_SEQUENCE')
        kind = message.get('type')
        if kind == 'http.response.start':
            require(self.status is None and type(message.get('status')) is int and
                    100 <= message['status'] <= 599, 'ASGI_START')
            require(type(message.get('headers')) is list, 'ASGI_HEADERS')
            require(message.get('trailers', False) is False and len(message['headers']) <= 64,
                    'ASGI_TRAILERS_OR_HEADERS')
            headers = {}
            for item in message['headers']:
                require(type(item) in (list, tuple) and len(item) == 2 and
                        all(type(x) is bytes for x in item), 'ASGI_HEADER_PAIR')
                name, value = item
                require(0 < len(name) <= 256 and len(value) <= 4096 and
                        re.fullmatch(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name) and
                        all(c >= 32 or c == 9 for c in value) and 127 not in value,
                        'ASGI_HEADER_SIZE')
                key = name.decode('latin1').lower()
                require(key not in headers, 'ASGI_DUPLICATE_HEADER')
                headers[key] = value.decode('latin1')
            self.status, self.headers = message['status'], headers
        else:
            require(kind == 'http.response.body' and self.status is not None, 'ASGI_BODY_ORDER')
            body, more = message.get('body', b''), message.get('more_body', False)
            require(type(body) is bytes and type(more) is bool, 'ASGI_BODY_TYPE')
            require(len(self.body) + len(body) <= 131072, 'ASGI_BODY_LIMIT')
            self.body.extend(body)
            self.finished = not more

    def result(self):
        require(self.finished, 'ASGI_INCOMPLETE')
        require(self.headers.get('content-type', '').split(';')[0].strip().lower() == 'application/json',
                'RESPONSE_CONTENT_TYPE')
        if 'content-length' in self.headers:
            require(self.headers['content-length'] == str(len(self.body)), 'CONTENT_LENGTH')
        return {'status': self.status, 'headers': self.headers, 'body': strict_json(bytes(self.body))}


def verify_response(case, response, spec):
    require(type(response['status']) is int and response['status'] == case['expect_status'], 'RESPONSE_STATUS')
    headers, body = response['headers'], response['body']
    require(headers.get('cache-control') == 'private, no-store, max-age=0' and
            headers.get('pragma') == 'no-cache' and headers.get('x-content-type-options') == 'nosniff' and
            headers.get('referrer-policy') == 'no-referrer' and
            not {'vercel-cdn-cache-control', 'cdn-cache-control'} & headers.keys(), 'RESPONSE_CACHE')
    if case['id'] in {'P1', 'P2', 'P5', 'P6'}:
        expected = {'P1': {'detail': 'missing bearer token'}, 'P2': {'detail': 'invalid bearer token'},
                    'P5': {'detail': {'code': 'RUNTIME_PROFILE_GAP',
                           'system_profile': case['query']['system_profile']}},
                    'P6': {'detail': 'Method Not Allowed'}}[case['id']]
        require(body == expected, 'RESPONSE_ERROR_BODY')
        return
    require(type(body) is dict and set(body) == PUBLIC_FIELDS, 'CATALOG_FIELDS')
    for name in ('formal_db_activation_asserted', 'fallback_performed'):
        require(body[name] is False, 'ACTIVATION_OR_FALLBACK')
    for name in ('engine_version', 'system_profile', 'authority_lane', 'lifecycle'):
        require(body[name] == spec['immutable_expectations'][name], 'CATALOG_IDENTITY')
    require(body['contract_version'] == 'CANON_KB_READ_V1' and body['snapshot_date'] == '2026-08-22'
            and body['snapshot_source'] == SNAPSHOT_SOURCE,
            'SNAPSHOT_IDENTITY')
    require(body['rule_id_fingerprint'] == spec['immutable_expectations']['registry_fingerprint'] and
            body['retrieval_status'] == 'RUNTIME_MATCH', 'REGISTRY_IDENTITY')
    require(type(body['count']) is int and type(body['rules']) is list and
            body['count'] == len(body['rules']), 'CATALOG_COUNT')
    for rule in body['rules']:
        require(type(rule) is dict and set(rule) == {'stable_key', 'executable'} and
                type(rule['stable_key']) is str and type(rule['executable']) is bool, 'RULE_TYPE')
    if case['id'] == 'P4':
        require(body['count'] == 1 and body['rules'] == [{'stable_key': UNBOUNDED, 'executable': False}],
                'UNBOUNDED_RULE')
    else:
        ids = [r['stable_key'] for r in body['rules']]
        require(len(ids) == len(set(ids)) == 111, 'REGISTRY_COUNT')
        require(hashlib.sha256('\n'.join(sorted(ids)).encode()).hexdigest() == body['rule_id_fingerprint'],
                'REGISTRY_CONTENT')
        require([r['stable_key'] for r in body['rules'] if not r['executable']] == [UNBOUNDED], 'EXECUTABLE_SCOPE')


async def request_actual(app, case):
    stream = ResponseStream()
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await asyncio.Event().wait()
    async def send(message):
        stream.feed(message)
    headers = [(b'host', b'packaged.invalid')]
    if case['auth'] != 'missing':
        token = TOKEN if case['auth'] == 'valid_local_synthetic' else 'wrong-local-only'
        headers.append((b'authorization', ('Bearer ' + token).encode('ascii')))
    path = case['path']
    scope = dict(type='http', asgi={'version': '3.0'}, http_version='1.1', method=case['method'],
        scheme='http', path=path, raw_path=path.encode(), root_path='',
        query_string=urlencode(case['query']).encode(), headers=headers,
        client=('127.0.0.1', 1234), server=('packaged.invalid', 80))
    await asyncio.wait_for(app(scope, receive, send), 3)
    return stream.result()


def run_packaged(args):
    spec = load_spec(args.spec)
    require(args.source_commit == SOURCE and args.source_tree == TREE and
            re.fullmatch('sha256:[0-9a-f]{64}', args.image_id), 'LAUNCH_IDENTITY')
    groups = platform_preflight()
    # -B prevents writes, not reads of stale .pyc. Force source compilation into
    # an absent readonly prefix, so copied CI compileall output cannot shadow pins.
    require(not Path('/qualification/no-bytecode').exists(), 'BYTECODE_PREFIX_PRESENT')
    sys.pycache_prefix = '/qualification/no-bytecode'
    before = verify_files(ROOT, spec['packaged_source_sha256'])
    driver_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    audit = IOAudit()
    profile = Counter()
    with asyncio.Runner() as runner:
        runner.get_loop()  # control channel established before application denials
        sys.addaudithook(audit)
        import psycopg
        def no_db(*unused, **kwargs):
            audit.deny('database')
        psycopg.connect = psycopg.Connection.connect = psycopg.AsyncConnection.connect = no_db
        controls = [('network', lambda: socket.socket().connect(('203.0.113.1', 9))),
                    ('database', lambda: psycopg.connect('not-a-dsn')),
                    ('write', lambda: open('/qualification-denied', 'wb'))]
        import subprocess
        controls.append(('process', lambda: subprocess.Popen([sys.executable, '-c', 'pass'])))
        for kind, action in controls:
            try:
                action()
            except Rejected as exc:
                require(str(exc) == 'FORBIDDEN_' + kind.upper(), 'CONTROL_WRONG_FAILURE')
            else:
                raise Rejected('CONTROL_DID_NOT_REJECT')
        require(audit.counts['controls'] == dict(network=1, database=1, write=1, process=1), 'CONTROL_COUNTS')
        audit.phase = 'application'
        sys.path.insert(0, '/app')
        entry = importlib.import_module('bridge_school_api.main')
        modules = [importlib.import_module('bridge_school_api.' + name) for name in
                   ('l1_canonical_runtime', 'l1_canonical_runtime_v2', 'l1_canonical_runtime_v3', 'l1_canonical_runtime_v4')]
        locations = {}
        for name in spec['packaged_source_sha256']:
            mod = importlib.import_module(name[:-3].replace('/', '.').removesuffix('.__init__'))
            require(Path(mod.__file__).resolve() == ROOT / name, 'MODULE_LOCATION')
            locations[name] = str(Path(mod.__file__).resolve())
        require(not entry.app.dependency_overrides, 'DEPENDENCY_OVERRIDES')
        knowledge = importlib.import_module('bridge_school_api.knowledge')
        route = [r for r in entry.app.routes if getattr(r, 'path', '') == '/v1/knowledge/runtime/l1']
        require(len(route) == 1 and route[0].endpoint is knowledge.l1_runtime_catalog and
                knowledge.evaluate is modules[-1].evaluate, 'ACTUAL_ROUTE_BINDING')
        codes = {m.evaluate.__code__: 'v' + str(i+1) for i, m in enumerate(modules)}
        def observe(frame, event, arg):
            if event == 'call' and frame.f_code in codes:
                profile[codes[frame.f_code]] += 1
        sys.setprofile(observe)
        threading.setprofile(observe)
        observations = []
        async def cases():
            for case in spec['cases']:
                require(len(observations) < 6, 'REQUEST_BUDGET')
                response = await request_actual(entry.app, case)
                verify_response(case, response, spec)
                observations.append({'id': case['id'], **response})
        try:
            runner.run(cases())
        finally:
            sys.setprofile(None)
            threading.setprofile(None)
        require(not audit.counts['application'] and len(observations) == 6 and
                all(profile['v'+str(i)] > 0 for i in range(1,5)), 'APPLICATION_BOUNDARY')
        require(not entry.app.dependency_overrides, 'DEPENDENCY_OVERRIDES')
        after = verify_files(ROOT, spec['packaged_source_sha256'])
        require(before == after and driver_sha == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'SOURCE_CHANGED')
        from importlib.metadata import version
        dependencies = {name: version(name) for name in ('fastapi', 'uvicorn', 'psycopg', 'starlette')}
        return dict(schema='packaged-l1-six-v1', status='PASS_PACKAGED_ASGI_ONLY',
            image_id=args.image_id, source_commit=SOURCE, source_tree=TREE, driver_sha256=driver_sha,
            spec_sha256=SPEC_SHA, packaged_root='/app', ASGI_object='bridge_school_api.main:app',
            files_before=before, files_after=after, module_locations=locations,
            bytecode_cache_mode='absent-readonly-prefix',
            cgroups=groups, machine=platform.machine(),
            observations=observations, public_evaluate_counts=dict(profile), dependencies=dependencies,
            guards={k: dict(v) for k,v in audit.counts.items()}, dependency_overrides=0,
            DB_connections=0, source_mount=False, listener_started=False,
            deployed_transport_qualified=False, learning_session_qualified=False, release_hold=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('spec', 'image-id', 'source-commit', 'source-tree'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args(argv)
    try:
        require(sys.platform == 'linux', 'LINUX_REQUIRED')
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(45)
        result = run_packaged(args)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False)
        require(len(encoded.encode()) <= 262144, 'OUTPUT_LIMIT')
        print(encoded, flush=True)
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, Rejected) else 'UNEXPECTED_FAILURE'
        print(json.dumps({'status': 'FAIL_CLOSED', 'code': code}), flush=True)
        return 2
    finally:
        if sys.platform == 'linux':
            signal.alarm(0)


if __name__ == '__main__':
    raise SystemExit(main())
