"""Fixed supervised journal-store preparation, independent of read-only transport."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import urllib.request

from ops import native_maintenance_bundle as bundle

REPOSITORY = 'olegmed1-art/bridge-video-free'
HOST = 'ubuntu@92.5.47.149'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError('REDIRECT_REFUSED')


def source_check(source):
    bundle.check(bundle.identifier(source, 40) and os.environ.get('GITHUB_SHA') == source
                 and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
                 and os.environ.get('GITHUB_REF') == 'refs/heads/main'
                 and os.environ.get('GITHUB_ACTOR') == 'olegmed1-art'
                 and os.environ.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', 'CONTEXT_REFUSED')
    token = os.environ.get('GH_TOKEN')
    bundle.check(bool(token), 'TOKEN_REQUIRED')
    url = 'https://api.github.com/repos/' + REPOSITORY + '/git/ref/heads/main'
    request = urllib.request.Request(url, headers={'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
        'Cache-Control': 'no-cache', 'User-Agent': 'native-store-prepare'})
    opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        bundle.check(response.status == 200 and response.url == url, 'MAIN_RESPONSE_REFUSED')
        raw = response.read(16385)
    bundle.check(len(raw) <= 16384, 'MAIN_RESPONSE_SIZE')
    value = json.loads(raw, object_pairs_hook=bundle.unique)
    bundle.check(value.get('ref') == 'refs/heads/main' and value.get('object', {}).get('type') == 'commit'
                 and value['object']['sha'] == source, 'MAIN_CHANGED')


def loader(name, content):
    encoded = base64.b64encode(content).decode('ascii')
    return (name + "=types.ModuleType(" + repr(name) + ")\nexec(compile(base64.b64decode("
            + repr(encoded) + ")," + repr(name) + ",'exec')," + name + ".__dict__)\n")


def bootstrap(repo, source, digest, run):
    lifetime = bundle.git(repo, 'show', source + ':ops/native_maintenance_lifetime.py')
    decoder = bundle.git(repo, 'show', source + ':ops/native_maintenance_bundle.py')
    bundle.check(0 < len(lifetime) <= 32768 and 0 < len(decoder) <= 32768, 'BOOTSTRAP_SIZE')
    code = ('import base64,types,sys\n' + loader('bundle', decoder)
            + 'payload=sys.stdin.buffer.read(bundle.MAX_WIRE+1)\n'
            + 'with bundle.extracted(payload,' + repr(source) + ',' + repr(digest) + ') as root:\n'
            + " sys.path.insert(0,str(root))\n from ops.native_maintenance_store import main\n main()\n")
    outer = ('import base64,types\n' + loader('lifetime', lifetime)
             + 'try:\n result=lifetime.managed(' + repr(code) + ','
             + repr(base64.b64encode(lifetime).decode()) + ',' + repr(source) + ',' + repr(run) + ')\n'
             + 'except BaseException:\n result=2\n'
             + 'raise SystemExit(result)\n')
    bundle.check(len(outer.encode()) <= 98304, 'BOOTSTRAP_SIZE')
    return outer


def main():
    bundle.check(len(sys.argv) == 3, 'ARGS_REFUSED')
    key, known_hosts = sys.argv[1:]
    source = os.environ.get('EXPECTED_MAIN')
    repo = Path(__file__).resolve().parents[1]
    source_check(source)
    run = os.environ.get('GITHUB_RUN_ID', '') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '')
    payload = bundle.build(repo, source)
    code = bootstrap(repo, source, bundle.digest(payload), run)
    command = ['ssh', '-F', '/dev/null', '-i', key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + known_hosts, '-o', 'ConnectTimeout=15',
               '-o', 'ConnectionAttempts=1', HOST,
               shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-S', '-c', code])]
    source_check(source)  # Last API observation immediately before the host operation.
    result = subprocess.run(command, input=payload, capture_output=True, timeout=115,
                            env={'PATH': '/usr/bin:/bin'})
    bundle.check(result.returncode == 0 and len(result.stdout) <= 512, 'HOST_PREPARE_REFUSED')
    record = json.loads(result.stdout, object_pairs_hook=bundle.unique)
    bundle.check(record in [dict(audit='NATIVE_STORE_PREPARED', filesystem=fs,
        journal_reopen=True, copy_restore=True, hold_unchanged=True,
        production_sql_mutations=False) for fs in ('ext4', 'xfs', 'btrfs')], 'HOST_RESULT_REFUSED')
    print(json.dumps({**record, 'source_sha': source}, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except BaseException:
        print(json.dumps({'audit': 'NATIVE_STORE_PREPARE_REFUSED'}))
        raise SystemExit(2) from None
