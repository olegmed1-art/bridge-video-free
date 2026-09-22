"""Attach only the previously provisioned fixed Light data disk; never format."""
import json
import os
import sys
import time
from oci_storage_audit import main as audit
from oci_light_access_audit import TENANCY, LIGHT, scalar, clients
from oci_light_data_volume_prepare import NAME, TOKEN

DEVICE = '/dev/oracleoci/oraclevdb'


def main():
    import oci
    block, boot_id, inventory = audit()
    assert inventory['allocated_gb'] <= 100
    source = block.get_boot_volume(boot_id).data
    rows = oci.pagination.list_call_get_all_results(block.list_volumes, compartment_id=TENANCY).data
    matches = [v for v in rows if v.display_name == NAME and v.lifecycle_state != 'TERMINATED']
    assert len(matches) == 1
    volume = matches[0]
    assert volume.freeform_tags.get('managed_by') == TOKEN
    assert volume.lifecycle_state == 'AVAILABLE' and volume.size_in_gbs == 50 and volume.vpus_per_gb == 10
    assert volume.availability_domain == source.availability_domain
    config = {k: scalar(os.environ[e], k) for k, e in (
        ('user', 'OCI_USER'), ('tenancy', 'OCI_TENANCY'),
        ('fingerprint', 'OCI_FINGERPRINT'), ('region', 'OCI_REGION'))}
    assert config['tenancy'] == TENANCY and config['region'] == 'eu-frankfurt-1'
    key = os.environ['OCI_KEY'].replace('\\r', '').replace('\\n', '\n')
    compute, _ = clients(config, key)
    rows = oci.pagination.list_call_get_all_results(compute.list_volume_attachments, compartment_id=TENANCY).data
    existing = [a for a in rows if a.volume_id == volume.id and a.lifecycle_state != 'DETACHED']
    assert len(existing) <= 1
    if existing:
        attachment = existing[0]
        assert attachment.instance_id == LIGHT and attachment.device == DEVICE
        assert not attachment.is_read_only and not attachment.is_shareable
        assert attachment.attachment_type == 'paravirtualized'
        assert attachment.is_pv_encryption_in_transit_enabled is True
    else:
        assert not any(a.instance_id == LIGHT and a.device == DEVICE and a.lifecycle_state != 'DETACHED' for a in rows)
        devices = oci.pagination.list_call_get_all_results(compute.list_instance_devices, LIGHT).data
        assert any(d.name == DEVICE and d.is_available for d in devices)
        attachment = compute.attach_volume(oci.core.models.AttachParavirtualizedVolumeDetails(
            instance_id=LIGHT, volume_id=volume.id, device=DEVICE,
            display_name='bridge-light-autopilot-data-attachment',
            is_read_only=False, is_shareable=False, is_pv_encryption_in_transit_enabled=True),
            opc_retry_token=TOKEN + '-attach').data
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        attachment = compute.get_volume_attachment(attachment.id).data
        print(json.dumps({'attachment': attachment.lifecycle_state, 'device': attachment.device,
                          'formatted_by_this_job': False}), flush=True)
        if attachment.lifecycle_state == 'ATTACHED':
            assert attachment.instance_id == LIGHT and attachment.volume_id == volume.id
            assert attachment.device == DEVICE and not attachment.is_read_only and not attachment.is_shareable
            assert attachment.attachment_type == 'paravirtualized'
            assert attachment.is_pv_encryption_in_transit_enabled is True
            return
        assert attachment.lifecycle_state == 'ATTACHING'
        time.sleep(20)
    raise TimeoutError('attachment pending')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'attachment': 'NOT_CONFIRMED', 'error_type': type(exc).__name__,
                          'http_status': getattr(exc, 'status', None)}))
        sys.exit(2)
