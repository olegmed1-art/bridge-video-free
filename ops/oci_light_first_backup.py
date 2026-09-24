"""One named system-disk backup; never delete, rotate or copy across regions."""
import json
import sys
import time
from oci_storage_audit import main as audit
from oci_light_access_audit import TENANCY

NAME = 'bridge-light-baseline-20260922'
TOKEN = 'bridge-light-baseline-backup-20260922-v1'


def main():
    import oci
    block, boot_id, inventory = audit()
    assert inventory['allocated_gb'] <= 200
    existing = oci.pagination.list_call_get_all_results(
        block.list_boot_volume_backups, compartment_id=TENANCY).data
    matches = [b for b in existing if b.display_name == NAME and b.lifecycle_state != 'TERMINATED']
    assert len(matches) <= 1
    if matches:
        backup = matches[0]
        assert backup.boot_volume_id == boot_id
        assert backup.freeform_tags.get('managed_by') == TOKEN
    else:
        assert inventory['backup_count'] < 5, 'free backup slots exhausted'
        assert all(b['state'] in {'AVAILABLE', 'FAULTY'} for b in inventory['backups']), 'other backup operation pending'
        backup = block.create_boot_volume_backup(
            oci.core.models.CreateBootVolumeBackupDetails(
                boot_volume_id=boot_id, display_name=NAME, type='FULL',
                freeform_tags={'managed_by': TOKEN}), opc_retry_token=TOKEN).data
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        backup = block.get_boot_volume_backup(backup.id).data
        print(json.dumps({'backup_name': NAME, 'state': backup.lifecycle_state,
                          'restore_test': 'NOT_PERFORMED'}), flush=True)
        if backup.lifecycle_state == 'AVAILABLE':
            return
        assert backup.lifecycle_state in {'REQUEST_RECEIVED', 'CREATING', 'FINALIZING'}
        time.sleep(20)
    raise TimeoutError('backup still pending; rerun resumes without another creation')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'backup': 'NOT_CONFIRMED', 'error_type': type(exc).__name__,
                          'http_status': getattr(exc, 'status', None)}))
        sys.exit(2)
