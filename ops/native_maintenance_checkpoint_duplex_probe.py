"""Fixed live synthetic SSH/OCI checkpoint rehearsal, never a permission runner."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_checkpoint_transport as rpc
from ops.native_maintenance_checkpoint_host_probe import identity
from ops.native_maintenance_readonly_transport import stop_group
from ops.native_maintenance_store_runner import HOST, loader, source_check
from ops.native_maintenance_workflow_pause import require
from ops.oci_light_access_audit import scalar

PHASE = 'startup'


def guard():
    require(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'DUPLEX_ACTOR')
    source_check(os.environ.get('EXPECTED_MAIN'))


def bootstrap(repo, source, source_digest, run_id, attempt):
    identity(source, run_id, attempt)
    lifetime = bundle.git(repo, 'show', source + ':ops/native_maintenance_lifetime.py')
    decoder = bundle.git(repo, 'show', source + ':ops/native_maintenance_bundle.py')
    require(0 < len(lifetime) <= 32768 and 0 < len(decoder) <= 32768, 'DUPLEX_BOOTSTRAP_SIZE')
    code = ('import base64,types,sys,json,os\n' + loader('bundle', decoder)
            + 'def read_exact(size):\n data=bytearray()\n while len(data)<size:\n'
            + "  part=os.read(0,min(65536,size-len(data)))\n  bundle.check(part,'DUPLEX_BOOTSTRAP_EOF')\n"
            + '  data.extend(part)\n return bytes(data)\n'
            + "size=int.from_bytes(read_exact(4),'big')\n"
            + "bundle.check(0<size<=4*1024*1024,'DUPLEX_BOOTSTRAP_FRAME')\n"
            + 'raw=read_exact(size)\nvalue=json.loads(raw,object_pairs_hook=bundle.unique)\n'
            + "bundle.check(type(value) is dict and set(value)=={'source'} and bundle.canonical(value)==raw,'DUPLEX_BOOTSTRAP_SCHEMA')\n"
            + "payload=base64.b64decode(value['source'],validate=True)\n"
            + 'with bundle.extracted(payload,' + repr(source) + ',' + repr(source_digest) + ') as root:\n'
            + " sys.path.insert(0,str(root))\n from ops.native_maintenance_checkpoint_host_probe import main\n"
            + ' main(' + repr(source) + ',' + repr(run_id) + ',' + repr(attempt) + ')\n')
    outer = ('import base64,types\n' + loader('lifetime', lifetime)
             + 'try:\n result=lifetime.managed(' + repr(code) + ','
             + repr(base64.b64encode(lifetime).decode()) + ',' + repr(source) + ','
             + repr(str(run_id) + '-' + str(attempt)) + ')\n'
             + 'except BaseException:\n result=2\nraise SystemExit(result)\n')
    require(len(outer.encode()) <= 98304, 'DUPLEX_BOOTSTRAP_SIZE')
    return outer


def validate_complete(record, source, binding, scope, sequence):
    require(type(record) is dict and set(record) == {'kind', 'binding', 'scope', 'source',
            'head_digest', 'sequence', 'elapsed_ms', 'hold_unchanged'}
            and record['kind'] == 'SYNTHETIC_DUPLEX_COMPLETE'
            and record['binding'] == binding and record['scope'] == scope and record['source'] == source
            and type(record['sequence']) is int and record['sequence'] == sequence
            and type(record['elapsed_ms']) is int and 0 <= record['elapsed_ms'] < 60000
            and record['hold_unchanged'] is True and bundle.identifier(record['head_digest'], 64),
            'DUPLEX_COMPLETE_REFUSED')


def main():
    global PHASE
    import oci
    from oci_storage_audit import main as audit
    require(len(sys.argv) == 3, 'DUPLEX_ARGS')
    guard()
    source = os.environ['EXPECTED_MAIN']
    run_id, attempt = int(os.environ['GITHUB_RUN_ID']), int(os.environ['GITHUB_RUN_ATTEMPT'])
    _, _, scope, binding = identity(source, run_id, attempt)
    PHASE = 'inventory'
    _, _, inventory = audit()
    require(inventory['compartments'] == 1 and inventory['allocated_gb'] <= 100, 'DUPLEX_INVENTORY')
    config = {k: scalar(os.environ[e], k) for k, e in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    require(config['tenancy'] == adapter.TENANCY and config['region'] == 'eu-frankfurt-1', 'DUPLEX_TARGET')
    config['key_content'] = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    client = oci.object_storage.ObjectStorageClient(config, timeout=(5, 10),
                                                   retry_strategy=oci.retry.NoneRetryStrategy())
    namespace = client.get_namespace(compartment_id=adapter.TENANCY,
                                    retry_strategy=oci.retry.NoneRetryStrategy()).data
    store = adapter.OCIJournalStore(client, namespace, guard)
    repo = Path(__file__).resolve().parents[1]
    payload = bundle.build(repo, source)
    code = bootstrap(repo, source, bundle.digest(payload), run_id, attempt)
    key, known_hosts = sys.argv[1:]
    command = ['ssh', '-F', '/dev/null', '-i', key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + known_hosts, '-o', 'ConnectTimeout=15',
               '-o', 'ConnectionAttempts=1', HOST,
               shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-S', '-c', code])]
    PHASE = 'supervised_duplex'
    guard()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, env={'PATH': '/usr/bin:/bin'}, start_new_session=True)
    started = time.monotonic()
    try:
        channel = rpc.Channel(process.stdout.fileno(), process.stdin.fileno(), binding, seconds=60)
        server = rpc.StoreServer(channel, scope, store, guard)
        channel.send(dict(source=base64.b64encode(payload).decode('ascii')))
        while True:
            record = channel.receive()
            if record.get('kind') == 'SYNTHETIC_DUPLEX_COMPLETE':
                validate_complete(record, source, binding, scope, server.sequence)
                break
            require(server.sequence < 128, 'DUPLEX_REQUEST_LIMIT')
            server.accept(record)
        channel.alive()
        elapsed_ms = int((time.monotonic() - started) * 1000)
        # Closing stdin forbids new requests; SSH must finish with the supervisor.
        process.stdin.close()
        require(process.wait(timeout=15) == 0, 'DUPLEX_HOST_EXIT')
        PHASE = 'independent_readback'
        guard()
        archive = checkpoint.accepted_latest(store, scope, record['head_digest'])
        head = store.read_head(scope)
        require(head is not None and checkpoint.sha(head[0]) == record['head_digest']
                and checkpoint.parse_head(head[0], scope)['sequence'] == 2, 'DUPLEX_FINAL_HEAD')
        guard()
    finally:
        if process.returncode is None:
            stop_group(process)
        if not process.stdin.closed:
            process.stdin.close()
        process.stdout.close()
    print(json.dumps(dict(audit='SYNTHETIC_SUPERVISED_DUPLEX_CHECKPOINT_PASS', source_sha=source,
                         scope_digest=scope, accepted_head_digest=record['head_digest'],
                         elapsed_ms=elapsed_ms, host_elapsed_ms=record['elapsed_ms'],
                         rpc_requests=server.sequence, archive_bytes=len(archive), hold_unchanged=True,
                         production_mutations=False, production_journal_backup=False,
                         full_executor_rehearsal=False, deleted_objects=0), sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException:
        print(json.dumps(dict(audit='SYNTHETIC_DUPLEX_CHECKPOINT_REFUSED', phase=PHASE)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    entrypoint()
