"""Fixed synthetic checkpoint/CAS/restore probe; never reads production journals."""
import json
import os
from pathlib import Path
import tempfile

from ops import native_maintenance_checkpoint as checkpoint
from ops import native_maintenance_checkpoint_oci as adapter
from ops import native_maintenance_snapshot as snapshot
from ops.native_maintenance_workflow_pause import Journal, digest, require
from ops.native_maintenance_store_runner import source_check
from ops.oci_light_access_audit import scalar

PHASE = 'startup'


def guard():
    require(os.environ.get('GITHUB_TRIGGERING_ACTOR') == 'olegmed1-art', 'PROBE_ACTOR')
    source_check(os.environ.get('EXPECTED_MAIN'))


def main():
    global PHASE
    import oci
    from oci_storage_audit import main as audit
    guard()
    PHASE = 'inventory'
    _, _, inventory = audit()
    require(inventory['compartments'] == 1 and inventory['allocated_gb'] <= 100, 'PROBE_INVENTORY')
    config = {k: scalar(os.environ[e], k) for k, e in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    require(config['tenancy'] == adapter.TENANCY and config['region'] == 'eu-frankfurt-1', 'PROBE_TARGET')
    config['key_content'] = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    client = oci.object_storage.ObjectStorageClient(config, timeout=(5, 10),
                                                   retry_strategy=oci.retry.NoneRetryStrategy())
    namespace = client.get_namespace(compartment_id=adapter.TENANCY,
                                    retry_strategy=oci.retry.NoneRetryStrategy()).data
    store = adapter.OCIJournalStore(client, namespace, guard)
    plan = dict(version=1, repository='olegmed1-art/bridge-video-free', source=os.environ['EXPECTED_MAIN'],
                workflows=[dict(id=1, path='.github/workflows/ci-only-synthetic.yml', state='active',
                                updated_at='2026-09-26T00:00:00Z')])
    scope = dict(source=plan['source'], workflow_plan_digest=digest(plan),
                 manifest_digest=digest({'probe': 'SYNTHETIC_ONLY_NOT_A_PERMISSION_MANIFEST'}),
                 probe='SYNTHETIC_CHECKPOINT_ONLY', run_id=int(os.environ['GITHUB_RUN_ID']),
                 attempt=int(os.environ['GITHUB_RUN_ATTEMPT']))
    require(scope['run_id'] > 0 and scope['attempt'] > 0, 'PROBE_RUN')
    binding = digest(scope)
    with tempfile.TemporaryDirectory(prefix='native-checkpoint-probe-') as directory:
        root = Path(directory)
        for name in snapshot.NAMES:
            (root / name).mkdir(mode=0o700)
        with Journal(root / 'operation') as operation, Journal(root / 'pause') as pause:
            operation.append(dict(kind='BOUND', scope=scope))
            pause.append(dict(kind='PLAN', plan=plan, digest=digest(plan), operation_scope_digest=binding))
            protocol = checkpoint.JournalCheckpoint(store)
            PHASE = 'initial_checkpoint'
            first = protocol.sync(binding, operation, pause)
            operation.append(dict(kind='SYNTHETIC_INTENT', outcome='UNKNOWN'))
            PHASE = 'successor_checkpoint'
            accepted = protocol.sync(binding, operation, pause)
            require(accepted != first, 'PROBE_HEAD_NOT_ADVANCED')
            PHASE = 'provider_cas_rejection'
            # Conditional attempt against the synthetic pointer only. A provider
            # accepting the wrong revision is a hard failure, never a real action.
            head = store.read_head(binding)
            guard()
            try:
                client.put_object(namespace, adapter.BUCKET, store._path(binding, 'head.json'), head[0],
                                  content_length=len(head[0]), if_match='invalid-probe-etag',
                                  retry_strategy=oci.retry.NoneRetryStrategy())
            except oci.exceptions.ServiceError as exc:
                require(exc.status == 412, 'PROBE_CAS_UNEXPECTED_ERROR')
            else:
                raise RuntimeError('PROBE_PROVIDER_CAS_NOT_ENFORCED')
            require(store.read_head(binding) == head, 'PROBE_CAS_CHANGED_HEAD')
            PHASE = 'download_restore'
            data = checkpoint.accepted_latest(store, binding, accepted)
            parent = root / 'restored'
            parent.mkdir(mode=0o700)
            restored = snapshot.restore(data, checkpoint.sha(data), binding, parent)
            require(snapshot.capture(restored / 'operation', restored / 'pause') == data
                    and snapshot.capture_locked(operation, pause) == data, 'PROBE_RESTORE')
            guard()
    print(json.dumps({'audit': 'SYNTHETIC_CHECKPOINT_OCI_RESTORE_PASS', 'source_sha': plan['source'],
                      'bytes': len(data), 'head_sequence': 2, 'provider_cas_rejected': True,
                      'accepted_head_digest': accepted, 'production_journal_backup': False,
                      'production_mutations': False, 'deleted_objects': 0}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        print(json.dumps({'audit': 'SYNTHETIC_CHECKPOINT_OCI_REFUSED', 'phase': PHASE}))
        raise SystemExit(2) from None
