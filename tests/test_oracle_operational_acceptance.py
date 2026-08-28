from pathlib import Path
import re


GATE = Path("ops/oracle_dds3_operational_gate.sh").read_text(encoding="utf-8")
WORKFLOW = Path(".github/workflows/oracle-operational-safety-gate.yml").read_text(encoding="utf-8")
MONITOR = Path(".github/workflows/oracle-ben-dds3-health-monitor.yml").read_text(encoding="utf-8")


def test_reboot_gate_requires_backup_and_checks_ben_services():
    assert '--wait-for-state AVAILABLE' in GATE
    assert '[[ "$BACKUP_STATE" == "AVAILABLE" ]]' in GATE
    assert '[[ "$ALLOW_REBOOT" == "1" ]]' in GATE
    assert "bridge-ben.service bridge-ben-healthcheck.timer" in GATE
    assert 'BOOT_ID_AFTER" != "$BOOT_ID_BEFORE' in GATE
    assert "bridge-dds3-operational-preflight" in GATE
    assert "HOST_MEMORY" in GATE
    assert "127.0.0.1:8085/bid" in GATE


def test_budget_recipient_is_secret_or_existing_oci_configuration():
    assert 'NONINTERACTIVE="${NONINTERACTIVE:-0}"' in GATE
    assert "existing OCI alert recipient is required" in GATE
    assert "OCI_BUDGET_EMAIL" in WORKFLOW
    assert "NONINTERACTIVE=1" in WORKFLOW
    assert not re.search(r"[A-Za-z0-9._%+-]+@(?:gmail|outlook|yahoo)\\.", WORKFLOW)


def test_operational_workflow_is_owner_gated_and_uses_run_command_control():
    assert "github.event.comment.user.login == 'olegmed1-art'" in WORKFLOW
    assert "/oracle-ops preflight" in WORKFLOW
    assert "/oracle-ops reboot" in WORKFLOW
    assert "/oracle-ops restore-preflight" in WORKFLOW
    assert 'SSH_KEY="$RUNNER_TEMP/no-ssh-control-key"' in WORKFLOW
    assert "oci-cli==3.90.3" in WORKFLOW


def test_scheduled_monitor_is_exact_host_bound_and_four_world_fail_closed():
    assert "cron: '23 */4 * * *'" in MONITOR
    assert "ops/oracle_known_hosts_from_scan.sh" in MONITOR
    assert "StrictHostKeyChecking=yes" in MONITOR
    assert "bridge-ben-healthcheck.timer" in MONITOR
    assert 'x.get("worlds") == 4' in MONITOR
    assert 'x.get("dds_required_worlds") == 4' in MONITOR
    assert 'x.get("fallback_used") is False' in MONITOR
    assert "[Monitor] Oracle BEN DDS3 health failure" in MONITOR


def test_restore_preflight_is_read_only_and_keeps_restore_unproven():
    start = WORKFLOW.index("- name: Read-only boot-volume restore preflight")
    end = WORKFLOW.index("- name: Publish sanitized evidence", start)
    restore = WORKFLOW[start:end]
    assert "oci bv boot-volume-backup list" in restore
    assert 'x.get("boot-volume-id")==sys.argv[1]' in restore
    assert '"restore_executed": False' in restore
    assert '"production_volume_modified": False' in restore
    assert "isolated_restore_and_acceptance_still_required" in restore
    assert "boot-volume create" not in restore
    assert "instance action" not in restore
    assert "boot-volume delete" not in restore


def test_oci_credentials_are_normalized_without_multiline_config_injection():
    assert 'def scalar(env_name, config_key):' in WORKFLOW
    assert 'expected one scalar or one {config_key}= entry' in WORKFLOW
    assert '"user": scalar("OCI_USER", "user")' in WORKFLOW
    assert '"tenancy": scalar("OCI_TENANCY", "tenancy")' in WORKFLOW
    assert 'cat > "$HOME/.oci/config"' not in WORKFLOW


def test_generated_oci_config_uses_real_line_separators():
    assert 'text = "[DEFAULT]\\\\\\\\n"' not in WORKFLOW
    assert 'key_file={Path.home() / \'.oci\' / \'key.pem\'}\\\\\\\\n' not in WORKFLOW
