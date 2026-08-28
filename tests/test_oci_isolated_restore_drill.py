from pathlib import Path

W = Path(".github/workflows/oci-isolated-restore-drill.yml").read_text(encoding="utf-8")

def test_isolated_restore_is_exact_owner_gated_and_production_safe():
    assert "github.event.issue.number == 265" in W
    assert "github.event.comment.user.login == 'olegmed1-art'" in W
    assert "github.event.comment.body == '/oracle-ops restore-isolated'" in W
    assert "--boot-volume-backup-id" in W
    assert "--wait-for-state AVAILABLE" in W
    assert '"production_volume_modified":False' in W
    assert '"routing_changed":False' in W
    assert '"temporary_volume_deleted":True' in W
    assert '"$state" == TERMINATED' in W
    assert '"boot_acceptance_proven":False' in W
    assert "instance action" not in W
    assert "boot-volume-attachment attach" not in W
