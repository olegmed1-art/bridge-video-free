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
