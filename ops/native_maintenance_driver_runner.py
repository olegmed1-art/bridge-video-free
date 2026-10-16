"""Fixed dependency-only root preparation; never loads an owner DB credential."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_driver as driver
from ops.native_maintenance_store_runner import HOST, loader, source_check

MAX_WIRE = 16 * 1024 * 1024


def bootstrap(repo, source, source_digest, wire_digest, run):
    lifetime = bundle.git(repo, 'show', source + ':ops/native_maintenance_lifetime.py')
    decoder = bundle.git(repo, 'show', source + ':ops/native_maintenance_bundle.py')
    bundle.check(0 < len(lifetime) <= 32768 and 0 < len(decoder) <= 32768, 'DRIVER_BOOTSTRAP_SIZE')
    code = ('import base64,types,sys,json,hashlib\n' + loader('bundle', decoder)
            + 'wire=sys.stdin.buffer.read(' + str(MAX_WIRE + 1) + ')\n'
            + 'bundle.check(len(wire)<=' + str(MAX_WIRE) + ' and hashlib.sha256(wire).hexdigest()=='
            + repr(wire_digest) + ",'DRIVER_WIRE_REFUSED')\n"
            + 'value=json.loads(wire,object_pairs_hook=bundle.unique)\n'
            + "bundle.check(type(value) is dict and set(value)=={'source','driver'},'DRIVER_ENVELOPE')\n"
            + "payload=base64.b64decode(value['source'],validate=True)\n"
            + 'with bundle.extracted(payload,' + repr(source) + ',' + repr(source_digest) + ') as root:\n'
            + " sys.path.insert(0,str(root))\n from ops.native_maintenance_driver import main\n"
            + " main(base64.b64decode(value['driver'],validate=True))\n")
    outer = ('import base64,types\n' + loader('lifetime', lifetime)
             + 'try:\n result=lifetime.managed(' + repr(code) + ','
             + repr(base64.b64encode(lifetime).decode()) + ',' + repr(source) + ',' + repr(run) + ')\n'
             + 'except BaseException:\n result=2\nraise SystemExit(result)\n')
    bundle.check(len(outer.encode()) <= 98304, 'DRIVER_BOOTSTRAP_SIZE')
    return outer


def main():
    bundle.check(len(sys.argv) == 4 and os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'DRIVER_ARGS')
    key, known_hosts, wheel_directory = sys.argv[1:]
    source = os.environ.get('EXPECTED_MAIN')
    repo = Path(__file__).resolve().parents[1]
    source_check(source)
    source_payload = bundle.build(repo, source)
    wheel_payload = driver.build(wheel_directory)
    wire = bundle.canonical(dict(source=base64.b64encode(source_payload).decode(),
                                 driver=base64.b64encode(wheel_payload).decode()))
    bundle.check(len(wire) <= MAX_WIRE, 'DRIVER_WIRE_SIZE')
    run = os.environ.get('GITHUB_RUN_ID', '') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '')
    code = bootstrap(repo, source, bundle.digest(source_payload), bundle.digest(wire), run)
    command = ['ssh', '-F', '/dev/null', '-i', key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + known_hosts, '-o', 'ConnectTimeout=15',
               '-o', 'ConnectionAttempts=1', HOST,
               shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-S', '-c', code])]
    source_check(source)
    result = subprocess.run(command, input=wire, capture_output=True, timeout=115,
                            env={'PATH': '/usr/bin:/bin'})
    bundle.check(result.returncode == 0 and len(result.stdout) <= 2048, 'DRIVER_HOST_REFUSED')
    value = json.loads(result.stdout, object_pairs_hook=bundle.unique)
    bundle.check(type(value) is dict and set(value) == {'audit', 'hold_unchanged', 'production_sql_mutations',
                 'runtime_id', 'filesystem', 'reused', 'installed_bytes', 'file_count', 'driver', 'impl', 'libpq'}
                 and value['audit'] == 'NATIVE_DRIVER_PREPARED' and value['hold_unchanged'] is True
                 and value['production_sql_mutations'] is False and bundle.identifier(value['runtime_id'], 64)
                 and value['filesystem'] in ('ext4', 'xfs', 'btrfs') and type(value['reused']) is bool
                 and type(value['installed_bytes']) is int and 0 < value['installed_bytes'] <= driver.MAX_EXPANDED
                 and type(value['file_count']) is int and 0 < value['file_count'] <= 512
                 and value['driver'] == '3.3.4' and value['impl'] == 'binary'
                 and type(value['libpq']) is int and value['libpq'] >= 170000, 'DRIVER_HOST_RESULT')
    source_check(source)
    print(json.dumps(dict(value, source_sha=source), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'audit': 'NATIVE_DRIVER_PREPARE_REFUSED'}))
        raise SystemExit(2) from None
