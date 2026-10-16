"""Synthetic journal off-VM recovery proof, never a production journal backup."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from ops.native_maintenance_workflow_pause import Journal, encoded, require, unique
from ops.native_maintenance_store_runner import source_check
from ops.oci_light_access_audit import TENANCY, scalar

BUCKET = 'bridge-light-autopilot-backups'
TAG = 'bridge-light-autopilot-backups-v1'
LIMIT = 8 * 1024**3
MAX_PAYLOAD = 65536
EVENTS = [{'kind': 'SYNTHETIC_BACKUP_PROBE', 'version': 1},
          {'kind': 'SYNTHETIC_PROBE_COMPLETE', 'version': 1}]
PHASE = 'startup'


def preflight():
    require(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'RERUN_ACTOR_REFUSED')
    source_check(os.environ.get('EXPECTED_MAIN'))


def payload(root):
    original = root / 'original'
    original.mkdir(mode=0o700)
    with Journal(original) as journal:
        for event in EVENTS:
            journal.append(event)
    records = [(original / f'{i:06d}.json').read_text() for i in range(len(EVENTS))]
    return encoded({'version': 1, 'scope': 'SYNTHETIC_ONLY', 'records': records})


def restore(data, root):
    require(type(data) is bytes and 0 < len(data) <= MAX_PAYLOAD, 'PROBE_SIZE')
    value = json.loads(data, object_pairs_hook=unique)
    require(type(value) is dict and set(value) == {'version', 'scope', 'records'}
            and type(value['version']) is int and value['version'] == 1
            and value['scope'] == 'SYNTHETIC_ONLY' and encoded(value) == data,
            'PROBE_SHAPE')
    records = value['records']
    require(type(records) is list and len(records) == len(EVENTS)
            and all(type(r) is str and len(r) < 16384 for r in records), 'PROBE_RECORDS')
    root.mkdir(mode=0o700)
    for i, record in enumerate(records):
        fd = os.open(root / f'{i:06d}.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as output:
            output.write(record)
            output.flush()
            os.fsync(output.fileno())
    with Journal(root) as journal:
        require([r['event'] for r in journal.records] == EVENTS, 'PROBE_EVENTS')
        journal.append({'kind': 'SYNTHETIC_RESTORED', 'version': 1})


def validate_bucket(detail):
    require(detail.compartment_id == TENANCY and detail.freeform_tags.get('managed_by') == TAG
            and detail.public_access_type == 'NoPublicAccess' and detail.storage_tier == 'Standard'
            and detail.versioning == 'Disabled' and detail.auto_tiering == 'Disabled'
            and not detail.is_read_only and not detail.kms_key_id, 'BACKUP_BUCKET_REFUSED')


def transfer(client, pages, namespace, data):
    """Only an immutable, already constructed synthetic archive is permitted."""
    global PHASE
    with tempfile.TemporaryDirectory(prefix='journal-probe-validation-') as directory:
        restore(data, Path(directory) / 'validated')
    size = len(data)
    digest = hashlib.sha256(data).hexdigest()
    PHASE = 'exact_budget'
    buckets = pages(client.list_buckets, namespace, compartment_id=TENANCY).data
    require(len({b.name for b in buckets}) == len(buckets), 'DUPLICATE_BUCKET')
    require(sum(b.name == BUCKET for b in buckets) == 1, 'EXISTING_BUCKET_REQUIRED')
    total = count = 0
    for bucket in buckets:
        detail = client.get_bucket(namespace, bucket.name).data
        require(detail.versioning == 'Disabled' and detail.storage_tier == 'Standard', 'BUDGET_INCOMPLETE')
        start, seen, object_names = None, set(), set()
        while True:
            page = client.list_objects(namespace, bucket.name, fields='name,size', limit=1000,
                                       **({'start': start} if start else {})).data
            require(all(type(o.size) is int and o.size >= 0 for o in page.objects), 'OBJECT_SIZE_INVALID')
            require(all(type(o.name) is str and o.name not in object_names for o in page.objects)
                    and len({o.name for o in page.objects}) == len(page.objects), 'DUPLICATE_OBJECT')
            object_names.update(o.name for o in page.objects)
            total += sum(o.size for o in page.objects)
            count += len(page.objects)
            require(count + 1 <= 10000 and total + size < LIMIT, 'FREE_BUDGET_REFUSED')
            start = page.next_start_with
            if not start: break
            require(type(start) is str and start not in seen, 'PAGINATION_LOOP')
            seen.add(start)
    PHASE = 'privacy'
    validate_bucket(client.get_bucket(namespace, BUCKET, fields=['autoTiering']).data)
    require(not pages(client.list_preauthenticated_requests, namespace, BUCKET).data, 'PUBLIC_LINK_REFUSED')
    require(not pages(client.list_replication_policies, namespace, BUCKET).data, 'REPLICATION_REFUSED')
    name = 'native-journal/synthetic-v1/' + digest + '.json'
    PHASE = 'immutable_identity'
    try:
        head = client.head_object(namespace, BUCKET, name)
    except Exception as exc:
        # Only an authenticated OCI object-not-found response permits creation.
        import oci
        if not isinstance(exc, oci.exceptions.ServiceError) or exc.status != 404:
            raise
        PHASE = 'immutable_upload'
        preflight()
        client.put_object(namespace, BUCKET, name, data, content_length=size, if_none_match='*',
                          content_type='application/json',
                          opc_meta={'sha256': digest, 'scope': 'synthetic-journal-only'})
    else:
        require(int(head.headers['content-length']) == size
                and head.headers.get('opc-meta-sha256') == digest, 'EXISTING_OBJECT_DRIFT')
    PHASE = 'download'
    response = client.get_object(namespace, BUCKET, name)
    require(int(response.headers['content-length']) == size, 'DOWNLOAD_SIZE')
    chunks, received = [], 0
    for chunk in response.data.raw.stream(16384, decode_content=False):
        received += len(chunk)
        require(received <= size, 'DOWNLOAD_OVERSIZE')
        chunks.append(chunk)
    downloaded = b''.join(chunks)
    require(received == size and hashlib.sha256(downloaded).hexdigest() == digest,
            'DOWNLOAD_DIGEST')
    validate_bucket(client.get_bucket(namespace, BUCKET, fields=['autoTiering']).data)
    require(not pages(client.list_preauthenticated_requests, namespace, BUCKET).data,
            'PUBLIC_LINK_REFUSED')
    return downloaded, total + size


def main():
    global PHASE
    import oci
    from oci_storage_audit import main as audit
    preflight()
    PHASE = 'inventory'
    _, _, inventory = audit()
    require(inventory['compartments'] == 1 and inventory['allocated_gb'] <= 100,
            'INVENTORY_SCOPE_REFUSED')
    config = {k: scalar(os.environ[e], k) for k, e in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    require(config['tenancy'] == TENANCY and config['region'] == 'eu-frankfurt-1', 'OCI_SCOPE_REFUSED')
    config['key_content'] = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    client = oci.object_storage.ObjectStorageClient(config, timeout=(10, 30),
                                                   retry_strategy=oci.retry.NoneRetryStrategy())
    namespace = client.get_namespace(compartment_id=TENANCY).data
    with tempfile.TemporaryDirectory(prefix='journal-off-vm-probe-') as directory:
        root = Path(directory)
        data = payload(root)
        downloaded, budget = transfer(client, oci.pagination.list_call_get_all_results, namespace, data)
        PHASE = 'restore'
        restore(downloaded, root / 'restored')
        with Journal(root / 'original') as journal:
            require([r['event'] for r in journal.records] == EVENTS, 'ORIGINAL_CHANGED')
    print(json.dumps({'audit': 'SYNTHETIC_JOURNAL_OFF_VM_RESTORE_PASS', 'bytes': len(data),
                      'observed_object_bytes_with_probe': budget, 'private_existing_bucket': True,
                      'production_journal_backup': False, 'light_modified': False,
                      'deleted_objects': 0, 'source_sha': os.environ['EXPECTED_MAIN']}, sort_keys=True))


if __name__ == '__main__':
    try: main()
    except BaseException:
        print(json.dumps({'audit': 'SYNTHETIC_JOURNAL_BACKUP_REFUSED', 'phase': PHASE}))
        raise SystemExit(2) from None
