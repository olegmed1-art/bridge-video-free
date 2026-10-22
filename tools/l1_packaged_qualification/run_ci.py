"""One exact-branch ephemeral build and six packaged requests; no retained artifact.

Import is offline. --execute requires a public GitHub Linux standard runner.
Build fetch has network; application execution uses the frozen network-none watchdog.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
import time
import uuid

HERE = Path(__file__).resolve().parent
module_spec = importlib.util.spec_from_file_location('l1_watchdog', HERE / 'packaged_l1_watchdog.py')
W = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = W
module_spec.loader.exec_module(W)
D = W.driver
BASE = 'python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2'
OWNER = 'school.qualification-build'
FROZEN = {'packaged_l1_driver.py': '1c41d6d052247091af7e8bdc94fbb243f9b5598287a3db1cdac2684a9fc766a6',
          'packaged_l1_watchdog.py': '7b9e792f172d2952d3f0b708e843a74ca2c696458ac536db864d5c09f1171ea0',
          'PACKAGED_L1_CHECK_SPEC.json': D.SPEC_SHA}
SCOPE = ['bridge_school_api', 'bridge_contracts', 'requirements-api.txt', 'Dockerfile.api'] + [
    'database/' + name + '.py' for name in ('video_staging_read', 'video_staging_lineage',
    'video_candidate_persistence', 'video_result_persistence', 'source_identity_persistence', 'runtime_worker_preflight')]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def checked(result, code):
    D.require(result.returncode == 0 and not result.timed_out and not result.overflow, code)
    return result.stdout


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def admit_event(event, env):
    ref = 'refs/heads/codex/l1-packaged-six-20261022'
    D.require(env.get('GITHUB_ACTIONS') == 'true' and env.get('GITHUB_EVENT_NAME') == 'push'
        and env.get('GITHUB_REF') == event.get('ref') == ref
        and re.fullmatch('[0-9a-f]{40}', env.get('GITHUB_SHA', ''))
        and env['GITHUB_SHA'] != '0' * 40 and event.get('deleted') is False
        and event.get('after') == env['GITHUB_SHA']
        and env.get('GITHUB_REPOSITORY') == 'olegmed1-art/bridge-video-free'
        and env.get('RUNNER_OS') == 'Linux' and env.get('RUNNER_ARCH') == 'X64', 'CI_EVENT')
    repo = event.get('repository', {})
    D.require(repo.get('id') == 1330085090 and repo.get('private') is False and
        repo.get('visibility') == 'public', 'PUBLIC_REPOSITORY_REQUIRED')


def verify_bundle():
    for name, digest in FROZEN.items():
        D.require(sha((HERE / name).read_bytes()) == digest, 'VERIFIER_PIN')
    spec = D.load_spec(HERE / 'PACKAGED_L1_CHECK_SPEC.json')
    pins = D.strict_json((HERE / 'WHEEL_PINS.json').read_bytes())
    rows = pins['wheels']
    D.require(len(rows) == len({r['name'] for r in rows}) == 21, 'LOCK_COUNT')
    expected = []
    for row in rows:
        D.require(re.fullmatch('[a-z0-9][a-z0-9-]*', row['name']) and
            re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', row['version']) and
            re.fullmatch('[0-9a-f]{64}', row['sha256']), 'LOCK_PIN')
        package = row['name'] + ({'uvicorn': '[standard]', 'psycopg': '[binary]'}.get(row['name'], ''))
        expected.append(package + '==' + row['version'] + ' --hash=sha256:' + row['sha256'])
    observed = [x for x in (HERE / 'requirements.lock').read_text('utf-8').splitlines()
                if x and not x.startswith('#')]
    D.require(observed == expected, 'LOCK_CONTENT')
    return spec, sha(canonical({name: sha((HERE / name).read_bytes()) for name in
                              ('requirements.lock', 'WHEEL_PINS.json')}))


def source_context(git, context, spec):
    D.require(git(['rev-parse', D.SOURCE + '^{tree}']).decode().strip() == D.TREE, 'SOURCE_TREE')
    entries = {}
    for record in git(['ls-tree', '-r', '-z', D.SOURCE, '--', *SCOPE]).split(b'\0'):
        if not record:
            continue
        header, raw_name = record.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        name = raw_name.decode('utf-8')
        p = PurePosixPath(name)
        D.require(mode == '100644' and kind == 'blob' and not p.is_absolute() and '..' not in p.parts
            and '__pycache__' not in p.parts and not name.endswith('.pyc'), 'SOURCE_ENTRY')
        entries[name] = oid
    D.require(set(spec['packaged_source_sha256']).issubset(entries), 'SOURCE_CLOSURE')
    archive = git(['archive', '--format=tar', D.SOURCE, '--', *SCOPE], out_limit=8388608)
    observed = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:') as tar:
        for member in tar.getmembers():
            if member.isdir():
                continue
            D.require(member.isfile() and member.name in entries and member.name not in observed
                and member.size <= 524288, 'ARCHIVE_ENTRY')
            raw = tar.extractfile(member).read()
            D.require(hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
                      == entries[member.name], 'GIT_BLOB')
            target = context / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            observed[member.name] = sha(raw)
    D.require(set(observed) == set(entries), 'SOURCE_MANIFEST')
    D.verify_files(context, spec['packaged_source_sha256'])
    for name in ('Dockerfile', 'requirements.lock', 'WHEEL_PINS.json', 'verify_install.py'):
        destination = 'Dockerfile.qualification' if name == 'Dockerfile' else name
        raw = (HERE / name).read_bytes()
        (context / destination).write_bytes(raw)
        observed[destination] = sha(raw)
    return observed


def document(raw, digest):
    # buildx --raw may append one terminal newline to the original registry blob.
    D.require(any('sha256:' + sha(value) == digest for value in (raw, raw.removesuffix(b'\n'))), 'REGISTRY_DOCUMENT_DIGEST')
    return D.strict_json(raw)


def amd64_child(index):
    rows = [x for x in index['manifests'] if x.get('platform', {}).get('os') == 'linux'
            and x['platform'].get('architecture') == 'amd64'
            and x['platform'].get('variant', '') in ('', 'v1')]
    D.require(len(rows) == 1 and W.IMAGE.fullmatch(rows[0]['digest']), 'BASE_PLATFORM')
    return rows[0]['digest']


def own_image(image, token):
    return (type(image) is dict and W.IMAGE.fullmatch(image.get('Id', '')) is not None and
            (image.get('Config', {}).get('Labels') or {}).get(OWNER) == token)


def remove_owned_image(call, identifier, token):
    value = W.decode_one(call(['image', 'inspect', identifier], timeout=3))
    D.require(own_image(value, token), 'IMAGE_CLEANUP_OWNERSHIP')
    # No force/prune, no base deletion and no foreign image ID.
    checked(call(['image', 'rm', value['Id']], timeout=3), 'IMAGE_CLEANUP_UNCONFIRMED')
    return 'OWNED_IMAGE_REMOVED'


def execute(repo, work, docker, env, git, deadline):
    revision = git(['rev-parse', 'HEAD']).decode().strip()
    D.require(revision == env['GITHUB_SHA'], 'CHECKED_OUT_REVISION')
    spec, dependencies = verify_bundle()
    context = work / 'context'
    context.mkdir()
    manifest = source_context(git, context, spec)
    context_sha = sha(canonical(manifest))
    token = uuid.uuid4().hex
    tag = 'l1-packaged-six:' + token
    config = work / 'docker-config'
    config.mkdir()
    command_env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'DOCKER_CONFIG': str(config),
                   'DOCKER_BUILDKIT': '1', 'BUILDX_NO_DEFAULT_ATTESTATIONS': '1'}
    prefix = [str(docker), '--host', 'unix:///var/run/docker.sock']
    def call(args, timeout=5, out_limit=262144, err_limit=65536):
        remaining = deadline - time.monotonic()
        D.require(remaining > 0, 'CI_COMMAND_DEADLINE')
        return W.capture(prefix + args, timeout=min(timeout, remaining), out_limit=out_limit,
                         err_limit=err_limit, env=command_env)
    record = dict(schema='l1-ci-ephemeral-six-v1', status='FAIL_CLOSED', source_commit=D.SOURCE,
        source_tree=D.TREE, qualification_revision=revision,
        CI_run=env['GITHUB_RUN_ID'], CI_attempt=env['GITHUB_RUN_ATTEMPT'],
        context_manifest=manifest, context_sha256=context_sha, dependencies_sha256=dependencies,
        image_tag=tag, build_owner=token, build_network='default-public-fetch',
        execution_network='none', artifact_upload=False, release_hold=True)
    attempted = False
    try:
        info = call(['info', '--format', '{{json .}}'])
        W.verify_daemon(D.strict_json(checked(info, 'DAEMON_PREFLIGHT')))
        raw_index = checked(call(['buildx', 'imagetools', 'inspect', BASE, '--raw'], 15, 1048576), 'BASE_INDEX_FETCH')
        index = document(raw_index, BASE.rsplit('@', 1)[1])
        child = amd64_child(index)
        child_ref = BASE.split('@')[0] + '@' + child
        raw_child = checked(call(['buildx', 'imagetools', 'inspect', child_ref, '--raw'], 15, 1048576), 'BASE_CHILD_FETCH')
        child_manifest = document(raw_child, child)
        record.update(base_index=BASE, base_child_digest=child, base_config_id=child_manifest['config']['digest'],
                      base_index_document=index, base_child_manifest=child_manifest)
        labels = {'org.opencontainers.image.revision': D.SOURCE, 'school.source-tree': D.TREE,
                  'school.context-sha256': context_sha, 'school.dependencies-sha256': dependencies, OWNER: token}
        args = ['build', '--platform=linux/amd64', '--progress=plain', '--pull', '--no-cache',
                '--network=default', '--file', str(context / 'Dockerfile.qualification'), '--tag', tag]
        for name, value in labels.items():
            args.extend(['--label', name + '=' + value])
        # Reserve the frozen watchdog's20s preflight+55s active window and image cleanup.
        build_budget = min(120, deadline - time.monotonic() - 90)
        D.require(build_budget > 0, 'BUILD_BUDGET_MISSING')
        attempted = True
        build = call(args + [str(context)], build_budget, 1048576, 1048576)
        print('L1_BUILD_LOG ' + json.dumps({'stdout': build.stdout.decode('utf-8', 'replace'),
            'stderr': build.stderr.decode('utf-8', 'replace')}), flush=True)
        checked(build, 'BUILD_FAILED_OR_UNCERTAIN_NO_RETRY')
        image = W.decode_one(call(['image', 'inspect', tag]))
        D.require(own_image(image, token), 'BUILT_IMAGE_OWNERSHIP')
        admission = dict(image_id=image['Id'], source_commit=D.SOURCE, source_tree=D.TREE,
            context_sha256=context_sha, dependencies_sha256=dependencies,
            driver_sha256=FROZEN['packaged_l1_driver.py'], docker_sha256=sha(docker.read_bytes()))
        W.verify_image(image, admission)
        record.update(image_inspect=image, docker_sha256=admission['docker_sha256'])
        D.require(deadline - time.monotonic() >= 80, 'SIX_BUDGET_MISSING')
        run = W.DockerRun(lambda args, timeout: call(args, timeout), admission, spec,
            HERE / 'packaged_l1_driver.py', HERE / 'PACKAGED_L1_CHECK_SPEC.json', uuid.uuid4().hex).run()
        record['six'] = run
        D.require(run['status'] == 'PASS_PACKAGED_ASGI_ONLY' and
                  run['cleanup'] == 'OWNED_STOPPED_CONTAINER_REMOVED', 'SIX_NOT_QUALIFIED')
        D.require(all(sha((context / name).read_bytes()) == digest for name, digest in manifest.items()), 'CONTEXT_CHANGED')
        record['status'] = 'PASS_PACKAGED_SIX_ONLY'
    except Exception as exc:
        record.update(status='FAIL_CLOSED', code=str(exc) if isinstance(exc, D.Rejected) else 'UNEXPECTED_FAILURE')
    finally:
        if attempted:
            try:
                record['image_cleanup'] = remove_owned_image(call, tag, token)
            except Exception:
                record.update(status='FAIL_CLOSED', image_cleanup='UNCONFIRMED_NO_FORCE_OR_RETRY')
        else:
            record['image_cleanup'] = 'NOT_BUILT'
        print('L1_PACKAGED_RESULT ' + json.dumps(record, ensure_ascii=True, allow_nan=False), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    D.require(args.execute and sys.platform == 'linux', 'EXPLICIT_LINUX_CI_REQUIRED')
    event = D.strict_json(Path(os.environ['GITHUB_EVENT_PATH']).read_bytes())
    admit_event(event, os.environ)
    repo = Path(os.environ['GITHUB_WORKSPACE']).resolve(strict=True)
    docker = Path(shutil.which('docker')).resolve(strict=True)
    git_bin = Path(shutil.which('git')).resolve(strict=True)
    deadline = time.monotonic() + 220
    def git(args, out_limit=262144):
        remaining = deadline - time.monotonic()
        D.require(remaining > 0, 'CI_SOURCE_DEADLINE')
        return checked(W.capture([str(git_bin), '-C', str(repo), *args], timeout=min(15, remaining),
                                 out_limit=out_limit), 'SOURCE_GIT')
    # Own temp paths only. No image export/save/upload/cache backend or registry push.
    with tempfile.TemporaryDirectory(prefix='l1-packaged-six-') as directory:
        result = execute(repo, Path(directory), docker, os.environ, git, deadline)
    return 0 if result['status'] == 'PASS_PACKAGED_SIX_ONLY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
