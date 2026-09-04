from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "ops/oracle_universal_video_root_filesystem_expand.sh").read_text()
WORKFLOW = (ROOT / ".github/workflows/issue-881-root-filesystem-expand.yml").read_text()


def test_expansion_is_partition_and_filesystem_specific() -> None:
    assert 'root_source="$(findmnt -n -o SOURCE --target /)"' in SCRIPT
    assert 'root_fstype="$(findmnt -n -o FSTYPE --target /)"' in SCRIPT
    assert 'root_type="$(lsblk -dnro TYPE "$root_source")"' in SCRIPT
    assert 'part_number="$(lsblk -dnro PARTN "$root_source")"' in SCRIPT
    assert 'growpart "$disk" "$part_number"' in SCRIPT
    assert 'resize2fs "$root_source"' in SCRIPT
    assert "xfs_growfs /" in SCRIPT


def test_no_format_or_oracle_lifecycle_commands() -> None:
    forbidden = ("mkfs", "wipefs", "fdisk", "parted", "reboot", "shutdown", "poweroff")
    assert not re.search(r"(?<![A-Za-z0-9_])(?:" + "|".join(forbidden) + r")(?![A-Za-z0-9_])", SCRIPT.lower())
    assert "systemctl" not in SCRIPT


def test_workflow_is_exact_host_one_shot_and_pinned() -> None:
    assert "ORACLE_HOST: 158.180.47.161" in WORKFLOW
    assert "EXPECTED_HOSTNAME: bridge-school-dds3-frankfurt" in WORKFLOW
    assert "EXPECTED_FINGERPRINT: SHA256:NXmGcng3fzof9b6Hs5Xgh4yYnzxGyVwa/EcfOxu0WPk" in WORKFLOW
    assert "EXACT_RUNTIME_SHA: bba508350cbe63a7a8ec93fa9c007db9ee9eae6c" in WORKFLOW
    assert "issue_comment:" in WORKFLOW
    assert "github.event.issue.number == 881" in WORKFLOW
    assert "github.event.comment.user.login == 'olegmed1-art'" in WORKFLOW
    assert '"/oracle-ops issue-881-expand-root-and-recover-bba508"' in WORKFLOW
    assert '"/oracle-ops issue-881-reconcile-run-33893910685"' in WORKFLOW


def test_postconditions_and_only_allowed_service_restart() -> None:
    assert "lsblk -f" in SCRIPT
    assert "findmnt /" in SCRIPT
    assert "df -h /" in SCRIPT
    assert "UV_ROOT_EXPAND_PASS" in SCRIPT
    assert "MIN_FREE_KB=5242880" in WORKFLOW
    assert "oracle_universal_video_container_missing_image_recover.sh" in WORKFLOW


def test_recovery_point_is_retained_and_restore_tested_before_mutation() -> None:
    checkpoint = WORKFLOW.index("Create and restore-test partition recovery point")
    retained = WORKFLOW.index("Retain recovery point")
    reconciled = WORKFLOW.index("Reconcile current main immediately before mutation")
    mutation = WORKFLOW.index("Expand root and recover exact image under shared host lock")
    assert checkpoint < retained < reconciled < mutation
    assert 'sfdisk --dump "$disk"' in SCRIPT
    assert 'sfdisk --verify "$disk"' in SCRIPT
    assert 'sudo sfdisk "$RUNNER_TEMP/restore-test.img"' in WORKFLOW
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in WORKFLOW


def test_all_mutation_tools_are_proven_before_growpart() -> None:
    grow = SCRIPT.index('growpart "$disk" "$part_number"')
    assert SCRIPT.index("command -v growpart") < grow
    assert SCRIPT.index("command -v resize2fs") < grow
    assert SCRIPT.index("command -v xfs_growfs") < grow


def test_already_grown_partition_resumes_at_filesystem_resize() -> None:
    assert 'if [[ "$partition_bytes_before" -lt "$minimum_expected_bytes" ]]' in SCRIPT
    assert 'partition_bytes_now="$(lsblk -bdnro SIZE "$root_source")"' in SCRIPT
    assert '[[ "$partition_bytes_now" -ge "$minimum_expected_bytes" ]]' in SCRIPT


def test_fresh_full_backup_and_isolated_boot_acceptance_gate_mutation() -> None:
    assert "timeout-minutes: 360" in WORKFLOW
    gate = WORKFLOW.index("Create fresh full backup and prove isolated restored-root boot acceptance")
    mutation = WORKFLOW.index("Expand root and recover exact image under shared host lock")
    assert gate < mutation
    assert "boot-volume-backup create" in WORKFLOW
    assert "--type FULL" in WORKFLOW
    assert "boot-volume create" in WORKFLOW
    assert "compute instance launch" in WORKFLOW
    assert "UV_RESTORED_ROOT_BOOT_ACCEPTANCE_PASS" in WORKFLOW
    assert "--preserve-boot-volume false" in WORKFLOW
    assert "temporary_instance_deleted=true" in WORKFLOW
    assert "temporary_volume_deleted=true" in WORKFLOW
    assert "total_seconds() < 86400" in WORKFLOW
    instance_fetch = WORKFLOW.index('instance_json="$(oci compute instance get')
    shape_read = WORKFLOW.index('shape="$(printf \'%s\' "$instance_json"')
    assert instance_fetch < shape_read
    assert "assert d['display-name']=='bridge-school-dds3-frankfurt'" in WORKFLOW
    assert "assert d['lifecycle-state']=='RUNNING'" in WORKFLOW
    assert "assert len(xs)==1; print(xs[0])" in WORKFLOW
    assert '[[ "$source_public_ip" == "$ORACLE_HOST" ]]' in WORKFLOW
    assert "network vcn create" in WORKFLOW
    assert "network security-list create" in WORKFLOW
    assert "--egress-security-rules '[]'" in WORKFLOW
    assert '\\"sourceType\\":\\"CIDR_BLOCK\\"' in WORKFLOW
    assert "network subnet delete" in WORKFLOW
    assert "isolated_no_egress_network=true" in WORKFLOW
    launch = WORKFLOW.index("oci compute instance launch")
    capture = WORKFLOW.index('drill_instance_id="$(printf', launch)
    wait = WORKFLOW.index("--wait-for-state RUNNING", capture)
    assert launch < capture < wait


def test_paid_and_temporary_creates_capture_before_separate_waits() -> None:
    block = WORKFLOW[
        WORKFLOW.index('backup_id="" restored_id=""') :
        WORKFLOW.index("Resolve pinned SSH identity")
    ]
    assert "discover_named_id()" in block
    assert 'discover_named_id backup "$backup_name"' in block
    assert "discover_named_id boot-volume" in block
    assert "discover_named_id instance" in block
    assert "discover_named_id vcn" in block
    assert "discover_named_id internet-gateway" in block
    assert "discover_named_id route-table" in block
    assert "discover_named_id security-list" in block
    assert "discover_named_id subnet" in block
    assert "assert len(xs)<=1" in block
    for line in block.splitlines():
        if re.search(r"oci (?:bv (?:boot-volume|boot-volume-backup)|network (?:vcn|internet-gateway|route-table|security-list|subnet)) create", line):
            assert "--wait-for-state" not in line
    for resource_get in (
        "boot-volume-backup get",
        "boot-volume get",
        "network vcn get",
        "network internet-gateway get",
        "network route-table get",
        "network security-list get",
        "network subnet get",
    ):
        assert resource_get in block
    assert block.count("--wait-for-state AVAILABLE") >= 7
    assert block.count("--wait-for-state AVAILABLE --max-wait-seconds 300") == 5
    assert "explicit OCI waiters consume at most 240 minutes" in WORKFLOW
    assert "least 90 minutes" in WORKFLOW


def test_cleanup_rediscovery_is_scoped_to_attempted_unique_resources() -> None:
    cleanup = WORKFLOW[WORKFLOW.index("cleanup_temp_resources()") :]
    for flag in (
        "restored_attempted",
        "drill_vcn_attempted",
        "drill_ig_attempted",
        "drill_route_attempted",
        "drill_security_attempted",
        "drill_subnet_attempted",
    ):
        assert f'"${flag}" == 1' in cleanup
    for name in (
        '"$stamp-restore"',
        '"$stamp-vcn"',
        '"$stamp-ig"',
        '"$stamp-route"',
        '"$stamp-ssh-only"',
        '"$stamp-subnet"',
    ):
        assert name in cleanup


def test_rerun_stamp_and_prior_attempt_reconciliation_precede_creation() -> None:
    block = WORKFLOW[
        WORKFLOW.index('boot_tag="$(printf') :
        WORKFLOW.index("Resolve pinned SSH identity")
    ]
    assert 'backup_prefix="issue-881-root-recovery-${boot_tag}"' in block
    assert 'operation_run_prefix="${backup_prefix}-run-"' in block
    assert 'run_prefix="${operation_run_prefix}${GITHUB_RUN_ID}"' in block
    assert 'prior_prefix="$operation_run_prefix"' in block
    assert 'stamp="${run_prefix}-a${GITHUB_RUN_ATTEMPT}"' in block
    assert 'backup_name="${backup_prefix}-${GITHUB_RUN_ID}-a${GITHUB_RUN_ATTEMPT}"' in block
    reconcile = block.index("reconcile_prior_attempt_resources")
    create = block.index("boot-volume-backup create")
    assert reconcile < create
    for resource in (
        "prior_instances",
        "prior_restored",
        "prior_subnets",
        "prior_security",
        "prior_routes",
        "prior_igs",
        "prior_vcns",
    ):
        assert resource in block
    assert "wait_all_absent" in block
    assert 'for id in "${ids[@]}"' in block
    assert "UV_ROOT_PRIOR_ATTEMPT_RECONCILIATION_PASS" in block
    assert "Keep TERMINATED objects in the evidence set" in block
    prior_filter = block[block.index("PRIOR_PREFIX=") : block.index("reconcile_prior_attempt_resources")]
    assert 'x.get("lifecycle-state") != "TERMINATED"' not in prior_filter
    assert 'drill_instance_id="$(discover_named_id instance "$stamp-boot-acceptance")" || failed=1' in block


def test_distinct_owner_commands_share_source_scoped_cleanup_identity() -> None:
    block = WORKFLOW[
        WORKFLOW.index('boot_tag="$(printf') :
        WORKFLOW.index("cleanup_temp_resources()")
    ]
    assert 'operation_run_prefix="${backup_prefix}-run-"' in block
    assert 'PRIOR_PREFIX="$prior_prefix"' in block
    assert 'CURRENT_STAMP="$stamp"' in block
    assert 'startswith(os.environ["PRIOR_PREFIX"])' in block
    assert 'not (x.get("display-name") or "").startswith(os.environ["CURRENT_STAMP"]+"-")' in block


def test_separate_commands_keep_one_operation_scoped_proven_backup() -> None:
    block = WORKFLOW[
        WORKFLOW.index('backup_prefix="issue-881-root-recovery-${boot_tag}"') :
        WORKFLOW.index("Resolve pinned SSH identity")
    ]
    lookup = block.index("select_fresh_operation_backup")
    create = block.index("boot-volume-backup create")
    assert lookup < create
    assert '--display-name "$backup_name"' in block
    assert '--display-name "$stamp-full"' not in block
    acceptance = block.index("UV_RESTORED_ROOT_BOOT_ACCEPTANCE_PASS")
    retire = block.index("superseded_operation_backups", acceptance)
    assert acceptance < retire
    assert "boot-volume-backup delete" in block[retire:]
    assert "wait_all_absent backup 60" in block[retire:]
    assert "superseded_backup_count=0" in block[retire:]


def test_failed_drill_deletes_only_its_new_unaccepted_backup() -> None:
    block = WORKFLOW[
        WORKFLOW.index('backup_id="" restored_id=""') :
        WORKFLOW.index("Resolve pinned SSH identity")
    ]
    assert "backup_attempted=0 backup_created_by_run=0 backup_accepted=0" in block
    assert "backup_attempted=1" in block
    assert '[[ -n "$backup_id" ]] && backup_created_by_run=1' in block
    assert '[[ -n "$backup_id" ]] || backup_id="$(discover_named_id backup "$backup_name")"' in block
    cleanup = block[
        block.index("cleanup() {") : block.index("\n          reconcile_prior_attempt_resources\n")
    ]
    assert "backup_created_by_run == 1 && backup_accepted == 0" in cleanup
    assert '[[ -z "$backup_id" && "$backup_attempted" == 1 ]]' in cleanup
    assert 'backup_id="$(discover_named_id backup "$backup_name")" || cleanup_rc=1' in cleanup
    assert "UV_ROOT_BACKUP_OWNERSHIP_UNRESOLVED" in cleanup
    assert cleanup.count("cleanup_rc=1") >= 3
    assert 'boot-volume-backup delete --boot-volume-backup-id "$backup_id"' in cleanup
    assert 'wait_absent backup "$backup_id" || cleanup_rc=1' in cleanup
    acceptance = block.index("UV_RESTORED_ROOT_BOOT_ACCEPTANCE_PASS")
    accepted = block.index("backup_accepted=1", acceptance)
    output = block.index('echo "backup_id=$backup_id" >> "$GITHUB_OUTPUT"')
    assert acceptance < accepted < output


def test_receipt_reports_backup_only_from_proven_step_output() -> None:
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "BACKUP_ID: ${{ steps.backup.outputs.backup_id }}" in receipt
    assert "backup_id = os.environ.get('BACKUP_ID') or state.get('retained_backup_id')" in receipt
    assert "AVAILABLE_FULL_97GB_REQUIRES_NEW_ISOLATED_BOOT_ACCEPTANCE" in receipt
    assert "'; accepted backup ID: '" in receipt
    assert "false_or_unproven" in receipt
    assert "SUPERSEDED_BACKUP_COUNT: ${{ steps.backup.outputs.superseded_backup_count }}" in receipt
    assert "superseded operation backups remaining" in receipt
    assert "fresh backup retained: `true` (billable" not in receipt


def test_temporary_restore_cleanup_waits_for_dependency_deletion() -> None:
    cleanup = WORKFLOW[WORKFLOW.index("wait_absent()") :]
    assert "wait_absent boot-volume" in cleanup
    assert "wait_absent subnet" in cleanup
    assert "wait_absent security-list" in cleanup
    assert "wait_absent route-table" in cleanup
    assert "wait_absent internet-gateway" in cleanup
    assert "wait_absent vcn" in cleanup
    assert "UV_ROOT_TEMP_CLEANUP_INCOMPLETE" in cleanup
    assert '--display-name "$stamp-boot-acceptance"' in cleanup
    assert "NotAuthorizedOrNotFound" in cleanup
    assert "404" in cleanup
    assert '[[ "$state" == TERMINATED ]]' in cleanup
    assert "UV_ROOT_RESOURCE_ABSENCE_PASS" in cleanup
    assert "status['\\\"]" in cleanup


def test_issue_881_retry_runs_only_after_guarded_expansion() -> None:
    mutation = WORKFLOW.index("Expand root and recover exact image under shared host lock")
    block = WORKFLOW[mutation:WORKFLOW.index("Publish bounded operational receipt")]
    assert block.index("oracle_universal_video_root_filesystem_expand.sh") < block.index(
        "oracle_universal_video_container_missing_image_recover.sh"
    )
    assert "/usr/bin/flock -x /run/lock/oracle-workload-mutation.lock /bin/bash -s" in block
    assert block.count("/run/lock/oracle-workload-mutation.lock") == 1
    assert "EXACT_RUNTIME_SHA: bba508350cbe63a7a8ec93fa9c007db9ee9eae6c" in WORKFLOW
    assert "'oracle-instance-workload-mutation'" in WORKFLOW
    assert 'systemctl restart "$SERVICE"' in (ROOT / "ops/oracle_universal_video_container_missing_image_recover.sh").read_text()


def test_last_second_gate_revalidates_oci_before_ssh_mutation() -> None:
    gate = WORKFLOW.index("Reconcile current main immediately before mutation")
    mutation = WORKFLOW.index("Expand root and recover exact image under shared host lock")
    block = WORKFLOW[gate:mutation]
    assert "boot-volume-attachment list" in block
    assert "boot-volume-backup get" in block
    assert "boot-volume get" in block
    assert '[[ "$boot_size" == 97 ]]' in block
    assert "assert d['display-name']=='bridge-school-dds3-frankfurt'" in block
    assert "assert d['lifecycle-state']=='RUNNING'" in block
    assert "lifecycle-state']=='AVAILABLE'" in block
    assert "total_seconds() < 86400" in block
    assert "vnic-attachment list" in block
    assert "network vnic get" in block
    assert '[[ "$source_public_ip" == "$ORACLE_HOST" ]]' in block


def test_failed_run_cleanup_is_exact_and_precedes_new_backup_or_mutation() -> None:
    cleanup_command = "/oracle-ops issue-881-reconcile-run-33893910685"
    assert cleanup_command in WORKFLOW
    assert 'target_prior_stamp="${operation_run_prefix}33893910685-a1"' in WORKFLOW
    assert 'failed_run_backup_name="${backup_prefix}-33893910685-a1"' in WORKFLOW
    cleanup = WORKFLOW.index("reconcile_prior_attempt_resources")
    backup_create = WORKFLOW.index("boot-volume-backup create")
    mutation = WORKFLOW.index("Expand root and recover exact image under shared host lock")
    assert cleanup < backup_create < mutation
    assert "FAILED_BACKUP_NAME" in WORKFLOW
    assert "failed_run_backup_cleanup_status RETAINED_AVAILABLE_REQUIRES_ACCEPTANCE" in WORKFLOW
    assert "assert data['lifecycle-state'] == 'AVAILABLE'" in WORKFLOW
    assert "allocation_summary" in WORKFLOW
    assert "cleanup_only=true" in WORKFLOW
    assert "EXACT_NAME_INVENTORY_EMPTY" in WORKFLOW
    assert "Each known ID must" in WORKFLOW


def test_receipt_is_literal_safe_and_retains_cleanup_evidence() -> None:
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "STATE_PATH" in receipt
    assert "json.loads(state_path.read_text())" in receipt
    assert "echo \"- workflow outcome:" not in receipt
    assert "failure receipt: https://github.com/olegmed1-art/bridge-video-free/issues/881#issuecomment-5543399377" in receipt
    for field in (
        "prior_resource_ids",
        "failed_run_backup_ids",
        "current_cleanup_status",
        "allocation_summary",
        "failure_rc",
    ):
        assert field in receipt
