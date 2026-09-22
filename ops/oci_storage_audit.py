"""Read-only complete home-region volume inventory; no names, keys or contents."""
import json
import os
import sys
from oci_light_access_audit import TENANCY, LIGHT, scalar


def main():
    import oci
    config = {k: scalar(os.environ[e], k) for k, e in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    assert config['tenancy'] == TENANCY and config['region'] == 'eu-frankfurt-1'
    config['key_content'] = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    kwargs = dict(timeout=(10, 30), retry_strategy=oci.retry.NoneRetryStrategy())
    iam = oci.identity.IdentityClient(config, **kwargs)
    block = oci.core.BlockstorageClient(config, **kwargs)
    compute = oci.core.ComputeClient(config, **kwargs)
    obj = oci.object_storage.ObjectStorageClient(config, **kwargs)
    def pages(fn, **kw):
        return oci.pagination.list_call_get_all_results(fn, **kw).data
    regions = pages(iam.list_region_subscriptions, tenancy_id=TENANCY)
    assert any(r.is_home_region and r.region_name == config['region'] for r in regions)
    compartments = [TENANCY] + [c.id for c in pages(iam.list_compartments,
        compartment_id=TENANCY, compartment_id_in_subtree=True, access_level='ANY')
        if c.lifecycle_state == 'ACTIVE']
    ads = pages(iam.list_availability_domains, compartment_id=TENANCY)
    instance = compute.get_instance(LIGHT).data
    assert instance.compartment_id == TENANCY and instance.lifecycle_state == 'RUNNING'
    attachments = pages(compute.list_boot_volume_attachments, compartment_id=TENANCY,
                        availability_domain=instance.availability_domain, instance_id=LIGHT)
    light_boot = {a.boot_volume_id for a in attachments if a.lifecycle_state == 'ATTACHED'}
    assert len(light_boot) == 1
    volumes, backups, buckets = [], [], []
    namespace = obj.get_namespace(compartment_id=TENANCY).data
    for cid in compartments:
        for ad in ads:
            for v in pages(block.list_boot_volumes, availability_domain=ad.name, compartment_id=cid):
                if v.lifecycle_state != 'TERMINATED':
                    volumes.append({'kind': 'boot', 'light': v.id in light_boot,
                                    'gb': v.size_in_gbs, 'state': v.lifecycle_state})
        for v in pages(block.list_volumes, compartment_id=cid):
            if v.lifecycle_state != 'TERMINATED':
                volumes.append({'kind': 'data', 'gb': v.size_in_gbs, 'state': v.lifecycle_state})
        for kind, fn in [('boot', block.list_boot_volume_backups), ('data', block.list_volume_backups)]:
            for b in pages(fn, compartment_id=cid):
                if b.lifecycle_state != 'TERMINATED':
                    backups.append({'kind': kind, 'state': b.lifecycle_state})
        for b in pages(obj.list_buckets, namespace_name=namespace, compartment_id=cid):
            detail = obj.get_bucket(namespace, b.name, fields=['approximateSize', 'approximateCount']).data
            buckets.append({'tier': detail.storage_tier, 'bytes': detail.approximate_size,
                            'objects': detail.approximate_count, 'versioning': detail.versioning})
    result = {'status': 'COMPLETE_READ_ONLY', 'home_region': config['region'],
              'compartments': len(compartments), 'volumes': volumes, 'backups': backups,
              'allocated_gb': sum(v['gb'] for v in volumes),
              'unallocated_of_200_gb': 200 - sum(v['gb'] for v in volumes),
              'backup_count': len(backups), 'buckets': buckets,
              'object_bytes_approximate': sum(b['bytes'] or 0 for b in buckets)}
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_type': type(exc).__name__,
                          'http_status': getattr(exc, 'status', None)}))
        sys.exit(2)
