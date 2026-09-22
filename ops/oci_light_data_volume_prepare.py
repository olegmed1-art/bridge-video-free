"""Create one 50 GB home-region data volume, without attaching or formatting it."""
import json
import sys
import time
from oci_storage_audit import main as audit
from oci_light_access_audit import TENANCY
from oci_light_first_backup import NAME as BACKUP_NAME, TOKEN as BACKUP_TOKEN

NAME = 'bridge-light-autopilot-data-50gb'
TOKEN = 'bridge-light-autopilot-data-20260922-v1'


def main():
    import oci
    block, boot_id, inventory = audit()
    source = block.get_boot_volume(boot_id).data
    backups = oci.pagination.list_call_get_all_results(block.list_boot_volume_backups,
                                                     compartment_id=TENANCY).data
    assert any(b.boot_volume_id == boot_id and b.display_name == BACKUP_NAME
               and b.freeform_tags.get('managed_by') == BACKUP_TOKEN
               and b.lifecycle_state == 'AVAILABLE' for b in backups), 'baseline backup absent'
    volumes = oci.pagination.list_call_get_all_results(block.list_volumes, compartment_id=TENANCY).data
    matches = [v for v in volumes if v.display_name == NAME and v.lifecycle_state != 'TERMINATED']
    assert len(matches) <= 1
    if matches:
        volume = matches[0]
        assert volume.freeform_tags.get('managed_by') == TOKEN
        assert volume.size_in_gbs == 50 and volume.availability_domain == source.availability_domain
        assert volume.vpus_per_gb == 10
        assert inventory['allocated_gb'] <= 100, 'restore reserve depleted'
    else:
        # Keep >=100 GB of the 200 GB allowance for side-by-side recovery.
        assert inventory['allocated_gb'] + 50 <= 100, 'restore reserve would be depleted'
        volume = block.create_volume(oci.core.models.CreateVolumeDetails(
            compartment_id=TENANCY, availability_domain=source.availability_domain,
            display_name=NAME, size_in_gbs=50, vpus_per_gb=10,
            freeform_tags={'managed_by': TOKEN}), opc_retry_token=TOKEN).data
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        volume = block.get_volume(volume.id).data
        print(json.dumps({'data_volume': NAME, 'size_gb': volume.size_in_gbs,
                          'state': volume.lifecycle_state, 'attached_by_this_job': False}), flush=True)
        if volume.lifecycle_state == 'AVAILABLE':
            assert volume.size_in_gbs == 50 and volume.vpus_per_gb == 10
            return
        assert volume.lifecycle_state == 'PROVISIONING'
        time.sleep(20)
    raise TimeoutError('volume still pending')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'data_volume': 'NOT_CONFIRMED', 'error_type': type(exc).__name__,
                          'http_status': getattr(exc, 'status', None)}))
        sys.exit(2)
