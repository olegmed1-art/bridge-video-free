"""Retain and restore an owner-read-only CANDIDATE; no permission execution."""
import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from ops import native_maintenance_bundle as bundle
from ops import native_maintenance_driver as driver
from ops import native_maintenance_recovery_assets as assets
from ops import native_maintenance_checkpoint_oci as adapter
from ops.native_maintenance_owner_host_runner import bootstrap, validate_report, MAX_WIRE
from ops.native_maintenance_store_runner import HOST, source_check
from ops.oracle_autopilot_source_preflight import connection_parameters
from ops.oci_light_access_audit import scalar

PHASE = 'startup'


def guard():
    bundle.check(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'ASSETS_ACTOR')
    source_check(os.environ.get('EXPECTED_MAIN'))


def candidate_response(raw):
    bundle.check(type(raw) is bytes and 0 < len(raw) <= 6 * 1024 * 1024, 'ASSETS_HOST_SIZE')
    value = json.loads(raw, object_pairs_hook=bundle.unique)
    bundle.check(type(value) is dict and set(value) == {'report', 'manifest'}, 'ASSETS_HOST_SCHEMA')
    validate_report(value['report'])
    encoded = value['manifest']
    bundle.check(type(encoded) is str and len(encoded) <= 4 * ((assets.MAX_MANIFEST + 2) // 3),
                 'ASSETS_HOST_MANIFEST_SIZE')
    manifest = base64.b64decode(encoded, validate=True)
    bundle.check(base64.b64encode(manifest).decode('ascii') == encoded, 'ASSETS_HOST_BASE64')
    assets.manifest(manifest, bundle.digest(manifest), value['report']['snapshot_digest'])
    return value['report'], manifest


def fetch_candidate(repo, source, key, known_hosts, wheel_directory, source_payload):
    credential = os.environ.pop('NATIVE_OWNER_DATABASE_URL', '')
    bundle.check(0 < len(credential) <= 8192, 'OWNER_CREDENTIAL_SIZE')
    connection_parameters(credential, 'neondb_owner')
    wheels = driver.build(wheel_directory)
    wire = bundle.canonical(dict(source=base64.b64encode(source_payload).decode('ascii'),
                                driver=base64.b64encode(wheels).decode('ascii'), credential=credential))
    bundle.check(len(wire) <= MAX_WIRE, 'ASSETS_WIRE_SIZE')
    run = os.environ.get('GITHUB_RUN_ID', '') + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '')
    code = bootstrap(repo, source, bundle.digest(source_payload), bundle.digest(wheels), run, candidate=True)
    command = ['ssh', '-F', '/dev/null', '-i', key, '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
               '-o', 'ForwardAgent=no', '-o', 'StrictHostKeyChecking=yes',
               '-o', 'UserKnownHostsFile=' + known_hosts, '-o', 'ConnectTimeout=15',
               '-o', 'ConnectionAttempts=1', HOST,
               shlex.join(['sudo', '-n', '/usr/bin/python3', '-I', '-B', '-S', '-c', code])]
    guard()
    result = subprocess.run(command, input=wire, capture_output=True, timeout=115,
                            env={'PATH': '/usr/bin:/bin'})
    bundle.check(result.returncode == 0, 'ASSETS_HOST_REFUSED')
    report, manifest = candidate_response(result.stdout)
    guard()
    return report, manifest


def main():
    global PHASE
    import oci
    from oci_storage_audit import main as audit
    bundle.check(len(sys.argv) == 4, 'ASSETS_ARGS')
    guard()
    source = os.environ['EXPECTED_MAIN']
    repo = Path(__file__).resolve().parents[1]
    source_payload = bundle.build(repo, source)
    PHASE = 'owner_read_only_candidate'
    report, manifest = fetch_candidate(repo, source, *sys.argv[1:], source_payload)
    identifiers = (source, bundle.digest(source_payload), bundle.digest(manifest), report['snapshot_digest'])
    data = assets.build(source_payload, manifest, *identifiers)
    envelope_digest = bundle.digest(data)
    PHASE = 'inventory'
    _, _, inventory = audit()
    bundle.check(inventory['compartments'] == 1 and inventory['allocated_gb'] <= 100, 'ASSETS_INVENTORY')
    config = {k: scalar(os.environ[e], k) for k, e in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    bundle.check(config['tenancy'] == adapter.TENANCY and config['region'] == 'eu-frankfurt-1', 'ASSETS_TARGET')
    config['key_content'] = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    client = oci.object_storage.ObjectStorageClient(config, timeout=(5, 10),
                                                   retry_strategy=oci.retry.NoneRetryStrategy())
    namespace = client.get_namespace(compartment_id=adapter.TENANCY,
                                    retry_strategy=oci.retry.NoneRetryStrategy()).data
    store = adapter.OCIJournalStore(client, namespace, guard)
    PHASE = 'retain_candidate'
    assets.retain(store, data, envelope_digest, *identifiers)
    PHASE = 'download_restore'
    downloaded = store.read_assets(envelope_digest, *identifiers)
    with tempfile.TemporaryDirectory(prefix='native-recovery-assets-') as directory:
        restored = assets.restore(downloaded, envelope_digest, *identifiers, Path(directory))
        bundle.check((restored / 'source.json').read_bytes() == source_payload
                     and (restored / 'manifest.json').read_bytes() == manifest, 'ASSETS_RESTORE_MISMATCH')
    guard()
    print(json.dumps(dict(audit='NATIVE_RECOVERY_CANDIDATE_RETAINED', source_sha=source,
                         source_digest=identifiers[1], manifest_digest=identifiers[2],
                         baseline_digest=identifiers[3], envelope_digest=envelope_digest,
                         runtime_id=report['runtime_id'], bytes=len(data), restored=True,
                         snapshot_approved=False, production_mutations=False,
                         production_journal_backup=False, deleted_objects=0), sort_keys=True))


def entrypoint():
    try:
        main()
    except BaseException:
        print(json.dumps(dict(audit='NATIVE_RECOVERY_ASSETS_REFUSED', phase=PHASE)))
        raise SystemExit(2) from None


if __name__ == '__main__':
    entrypoint()
