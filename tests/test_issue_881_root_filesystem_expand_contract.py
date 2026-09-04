from pathlib import Path
import datetime
import json
import re
import os
import subprocess
import tempfile
import textwrap


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
    assert WORKFLOW.count("assert 0 <= age < 86400") == 2
    instance_fetch = WORKFLOW.index('oci_json_request oci compute instance get')
    shape_read = WORKFLOW.index('shape="$(printf \'%s\' "$instance_json"')
    assert instance_fetch < shape_read
    assert "assert d['display-name']=='bridge-school-dds3-frankfurt'" in WORKFLOW


def test_monotonic_budget_reserves_cleanup_mutation_receipt_and_runner_slack() -> None:
    assert "Establish monotonic operation and cleanup budgets" in WORKFLOW
    assert 'primary_deadline=$((monotonic_now + 14400))' in WORKFLOW
    assert 'cleanup_deadline=$((monotonic_now + 16800))' in WORKFLOW
    assert 'mutation_deadline=$((monotonic_now + 19200))' in WORKFLOW
    assert 'receipt_deadline=$((monotonic_now + 20700))' in WORKFLOW
    assert 14400 + 2400 + 2400 + 1500 + 900 == 360 * 60

    # Worst-case current-attempt cleanup: eight 30s exact-name rediscoveries,
    # eight 20s delete requests, a 10m instance wait, and seven 2m dependent
    # resource/backup waits. This fits the executable 40m cleanup reserve.
    cleanup_worst_case_seconds = 8 * 30 + 8 * 20 + 600 + 7 * 120
    assert cleanup_worst_case_seconds == 1840
    assert cleanup_worst_case_seconds < 16800 - 14400
    for exact in (
        'discover_named_id boot-volume "$stamp-restore" 30 "$cleanup_deadline"',
        'discover_named_id vcn "$stamp-vcn" 30 "$cleanup_deadline"',
        'discover_named_id instance "$stamp-boot-acceptance" 30 "$cleanup_deadline"',
        'discover_named_id backup "$backup_name" 30 "$cleanup_deadline"',
    ):
        assert exact in WORKFLOW
    assert "if (( prior_count > 20 ))" in WORKFLOW
    assert 'wait_all_absent instance 600' in WORKFLOW
    assert WORKFLOW.count('wait_all_absent boot-volume 120') >= 1
    assert 'bounded_wait_seconds 20 "$cleanup_deadline"' in WORKFLOW
    assert 'timeout --signal=KILL "${delete_timeout}s" "${command[@]}"' in WORKFLOW

    helper_start = WORKFLOW.index("monotonic_now() { awk")
    helper_end = WORKFLOW.index("for value in OCI_USER", helper_start)
    helpers = textwrap.dedent(WORKFLOW[helper_start:helper_end])
    probe = subprocess.run(
        [
            "bash",
            "-c",
            "set -e\n" + helpers + r'''
now="$(monotonic_now)"
if bounded_wait_seconds 10 "$((now - 1))" >/dev/null; then primary_rc=0; else primary_rc=$?; fi
cleanup_value="$(bounded_wait_seconds 10 "$((now + 20))")"
printf 'primary_rc=%s cleanup_value=%s\n' "$primary_rc" "$cleanup_value"
''',
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "primary_rc=42 cleanup_value=10" in probe.stdout


def test_operational_receipt_survives_missing_budget_outputs() -> None:
    receipt_start = WORKFLOW.index(
        'STATE_PATH="$RUNNER_TEMP/issue-881-step4-state.json" BODY_PATH="$body" RECEIPT_MONOTONIC_NOW="$receipt_monotonic_now" python - <<\'PY\''
    )
    python_start = WORKFLOW.index("\n", receipt_start) + 1
    python_end = WORKFLOW.index("\n          PY", python_start)
    receipt_program = textwrap.dedent(WORKFLOW[python_start:python_end])
    with tempfile.TemporaryDirectory() as temp_dir:
        body_path = Path(temp_dir) / "receipt.md"
        result = subprocess.run(
            ["python", "-c", receipt_program],
            env=os.environ
            | {
                "STATE_PATH": str(Path(temp_dir) / "missing-state.json"),
                "BODY_PATH": str(body_path),
                "RECEIPT_MONOTONIC_NOW": "123",
                "JOB_STATUS": "failure",
                "BACKUP_OUTCOME": "failure",
                "BACKUP_ID": "",
                "SUPERSEDED_BACKUP_COUNT": "",
                "TEMP_INSTANCE_SECONDS": "",
                "MUTATION_OUTCOME": "skipped",
                "RUN_URL": "https://example.invalid/run",
            },
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        receipt = body_path.read_text()
        assert "monotonic primary/cleanup/mutation/receipt deadlines: `unknown/unknown/unknown/unknown`" in receipt
        assert "receipt started inside reserved deadline: `false`" in receipt
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
    wait = WORKFLOW.index('wait_oci_resource_ready instance "$drill_instance_id" RUNNING', capture)
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
    assert "--wait-for-state" not in block
    assert 'if wait_backup_available "$backup_id"' in block
    assert block.count("wait_oci_resource_ready") >= 8
    assert 'restored_gate_seconds="$(bounded_wait_seconds 3600 "$primary_deadline")"' in block
    assert 'wait_oci_resource_ready boot-volume "$restored_id" AVAILABLE "$restored_available_seconds"' in block
    assert 'wait_oci_resource_ready instance "$drill_instance_id" RUNNING 1800' in block
    assert "at 240 minutes total" in WORKFLOW
    assert "current-attempt cleanup worst case is 1,840 seconds" in WORKFLOW
    assert "40-minute cleanup" in WORKFLOW
    assert "15 minutes for runner setup/cancellation slack" in WORKFLOW


def test_operational_oci_reads_have_no_raw_wait_or_command_substitution_boundary() -> None:
    backup_phase = WORKFLOW[
        WORKFLOW.index("Create fresh full backup and prove isolated restored-root boot acceptance") :
        WORKFLOW.index("Resolve pinned SSH identity")
    ]
    last_second_phase = WORKFLOW[
        WORKFLOW.index("Reconcile current main immediately before mutation") :
        WORKFLOW.index("Expand root and recover exact image under shared host lock")
    ]
    for phase in (backup_phase, last_second_phase):
        assert "--wait-for-state" not in phase
        assert "--raw-output" not in phase
        assert not re.search(r"\$\(\s*oci\s", phase)
    assert "# END OCI_JSON_REQUEST_HELPER" in backup_phase


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
    assert 'drill_instance_id="$(discover_named_id instance "$stamp-boot-acceptance" 30 "$cleanup_deadline")" || failed=1' in block


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
    assert "delete_resource_once backup" in block[retire:]
    assert "wait_all_absent backup 120" in block[retire:]
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
    assert 'backup_id="$(discover_named_id backup "$backup_name" 30 "$cleanup_deadline")" || cleanup_rc=1' in cleanup
    assert "UV_ROOT_BACKUP_OWNERSHIP_UNRESOLVED" in cleanup
    assert cleanup.count("cleanup_rc=1") >= 3
    assert 'delete_resource_once backup "$backup_id"' in cleanup
    assert 'wait_absent backup "$backup_id" 120 || cleanup_rc=1' in cleanup
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
    for prefix in (
        "vcn_create",
        "internet_gateway_create",
        "route_table_create",
        "security_list_create",
        "subnet_create",
        "instance_create",
    ):
        assert f"value('{prefix}_status'" in receipt
        assert f"value('{prefix}_failure_rc'" in receipt


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


def test_oci_json_stdout_isolated_from_warning_stderr() -> None:
    helper = WORKFLOW[WORKFLOW.index("oci_json_request()") : WORKFLOW.index("operation_backup_inventory()")]
    assert '>"$stdout_file" 2>"$stderr_file"' in helper
    assert 'OCI_JSON_OUTPUT="$(<"$stdout_file")"' in helper
    assert 'OCI_JSON_ERROR="$(<"$stderr_file")"' in helper
    assert "2>&1" not in helper
    assert 'max_attempts="${OCI_JSON_MAX_ATTEMPTS:-6}"' in helper
    assert 'retry_delay_seconds="${OCI_JSON_RETRY_DELAY_SECONDS:-5}"' in helper
    assert 'for attempt in $(seq 1 "$max_attempts")' in helper
    assert "INVALID_JSON_SUCCESS_RESPONSE" in helper
    assert 'bounded_retry_seconds="$(bounded_wait_seconds "$retry_delay_seconds" "$absolute_deadline")"' in helper
    assert 'sleep "$bounded_retry_seconds"' in helper
    assert "return 86" in helper
    assert "(( rc == 0 )) || return" in helper
    assert "json.load(sys.stdin)" in helper

    cleanup = WORKFLOW[WORKFLOW.index("wait_absent()") : WORKFLOW.index("operation_backup_inventory()")]
    assert 'oci_json_request "${command[@]}"; then' in cleanup
    assert 'instance) command=(oci compute instance get' in cleanup
    assert "2>&1" not in cleanup

    direct_get = WORKFLOW[
        WORKFLOW.index("Preserve every exact-name backup ID") :
        WORKFLOW.index("boot_inventory=", WORKFLOW.index("Preserve every exact-name backup ID"))
    ]
    assert "if oci_json_request oci bv boot-volume-backup get" in direct_get
    assert "failed_backup_output=\"$OCI_JSON_OUTPUT\"" in direct_get
    assert "failed_backup_output=\"$OCI_JSON_ERROR\"" in direct_get
    assert "2>&1" not in direct_get

    allocation = WORKFLOW[WORKFLOW.index("boot_inventory=") - 160 : WORKFLOW.index("allocation_summary=")]
    assert "oci_json_request oci bv boot-volume list" in allocation
    assert "oci_json_request oci bv volume list" in allocation
    assert "--raw-output" not in allocation
    assert "2>&1" not in allocation


def test_backup_inventory_and_selection_are_validated_and_receipted() -> None:
    selection = WORKFLOW[
        WORKFLOW.index("mark_phase inventory_operation_backups") :
        WORKFLOW.index('if [[ -z "$backup_id" ]]', WORKFLOW.index("mark_phase inventory_operation_backups"))
    ]
    assert "if oci_json_request oci bv boot-volume-backup list" in selection
    assert "INVENTORY_REQUEST_FAILED" in selection
    assert "select_fresh_operation_backups" in selection
    assert "backup_candidate_ids" in selection
    assert "backup_candidate_count" in selection
    assert "backup_ineligible_candidate_ids" in selection
    assert "backup_invalid_candidate_ids" in selection
    assert "INVALID_OPERATION_BACKUP_METADATA" in selection
    assert "ACTIVE_TRANSIENT_OR_FAULTY_BACKUP_BLOCKS_CREATE" in WORKFLOW
    assert WORKFLOW.index("ACTIVE_TRANSIENT_OR_FAULTY_BACKUP_BLOCKS_CREATE") < WORKFLOW.index('if [[ -z "$backup_id" ]]')
    assert "REUSED_NEWEST_VALID_CANDIDATE" in WORKFLOW
    assert "backup_inventory=\"$(operation_backup_inventory)\"" not in selection

    selector = WORKFLOW[WORKFLOW.index("select_fresh_operation_backups()") : WORKFLOW.index("superseded_operation_backups()")]
    assert "candidates.sort(reverse=True)" in selector
    assert '"candidate_ids"' in selector
    assert '"blocking_ids"' in selector
    assert '"ineligible_ids"' in selector
    assert '"invalid_ids"' in selector
    assert '"selected_id"' in selector
    assert "assert isinstance(data,list)" in selector
    assert "if age < 0: invalid.append(backup_id)" in selector
    assert 'age < 86400 else ineligible' in selector
    assert 'age < 86400 else invalid' not in selector

    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "backup selection status" in receipt
    assert "backup selection failure rc" in receipt
    assert "reusable backup candidate count" in receipt
    assert "reusable backup candidate IDs" in receipt
    assert "ineligible backup candidate IDs" in receipt
    assert "invalid backup metadata IDs" in receipt


def test_selected_backup_wait_uses_validated_direct_get_state_machine() -> None:
    helper = WORKFLOW[WORKFLOW.index("wait_backup_available()") : WORKFLOW.index("select_fresh_operation_backups()")]
    assert "oci_json_request oci bv boot-volume-backup get" in helper
    assert "BACKUP_AVAILABLE_JSON" in helper
    assert "INVALID_GET_PAYLOAD" in helper
    assert '[[ "$state" == AVAILABLE ]]' in helper
    assert "deadline=$((SECONDS + max_seconds))" in helper
    assert "OCI_JSON_REQUEST_TIMEOUT_SECONDS=\"$request_timeout\"" in helper
    assert "remaining=$((deadline - SECONDS))" in helper
    assert "sleep_seconds=$((remaining < 5 ? remaining : 5))" in helper
    assert '[[ "$state" == TERMINATING || "$state" == TERMINATED || "$state" == FAULTY ]]' in helper
    assert "backup_wait_status UNKNOWN_STATE" in helper
    assert "backup_wait_last_state UNKNOWN" in helper
    assert "backup_wait_last_state INVALID" in helper
    assert 'allowed={"AVAILABLE","CREATING","REQUEST_RECEIVED","TERMINATING","TERMINATED","FAULTY"}' in helper
    assert 's if isinstance(s,str) and s in allowed' in helper
    assert "backup_wait_status TIMEOUT" in helper
    assert "return 41" in helper
    assert "return 42" in helper
    assert "return 43" in helper

    gate = WORKFLOW[
        WORKFLOW.index("mark_phase wait_selected_backup_available") :
        WORKFLOW.index("restored_attempted=1", WORKFLOW.index("mark_phase wait_selected_backup_available"))
    ]
    assert 'if wait_backup_available "$backup_id"' in gate
    assert 'backup_json="$BACKUP_AVAILABLE_JSON"' in gate
    assert "--wait-for-state AVAILABLE" not in gate
    assert 'backup_json="$(oci bv boot-volume-backup get' not in gate
    assert "assert 0 <= age < 86400" in gate

    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "backup availability wait" in receipt
    assert "backup availability last state" in receipt
    assert "backup availability failure rc" in receipt


def test_all_resource_readiness_is_identity_bound_and_adversarially_bounded() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        oci_start = WORKFLOW.index("oci_json_request() {")
        oci_end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", oci_start)
        wait_start = WORKFLOW.index("wait_oci_resource_ready() {")
        wait_end = WORKFLOW.index("wait_attached_vnic()", wait_start)
        helpers = textwrap.dedent(WORKFLOW[oci_start:oci_end] + WORKFLOW[wait_start:wait_end])
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
case "$FAKE_MODE" in
  slow) sleep 5; echo '{}' ;;
  wrong_id) echo '{"data":{"id":"other","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","lifecycle-state":"AVAILABLE"}}' ;;
  wrong_name) echo '{"data":{"id":"expected-id","display-name":"other","compartment-id":"tenancy","availability-domain":"ad","vcn-id":"vcn","lifecycle-state":"AVAILABLE"}}' ;;
  wrong_compartment) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"other","availability-domain":"ad","vcn-id":"vcn","lifecycle-state":"AVAILABLE"}}' ;;
  wrong_ad) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"other","vcn-id":"vcn","lifecycle-state":"AVAILABLE"}}' ;;
  wrong_vcn) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","vcn-id":"other","lifecycle-state":"AVAILABLE"}}' ;;
  null) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","vcn-id":"vcn","lifecycle-state":null}}' ;;
  injected) printf '%s\n' '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","vcn-id":"vcn","lifecycle-state":"ALIEN\\nINJECTED"}}' ;;
  unknown) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","lifecycle-state":"ALIEN"}}' ;;
  terminal) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","lifecycle-state":"FAULTY"}}' ;;
  auth) echo '{"code":"NotAuthenticated","status":401}' >&2; exit 17 ;;
  malformed) echo '{malformed' ;;
  available) echo '{"data":{"id":"expected-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","vcn-id":"vcn","lifecycle-state":"AVAILABLE"}}' ;;
esac
"""
        )
        fake_oci.chmod(0o755)
        script = "set -e\n" + helpers + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
bounded_wait_seconds() { printf '%s\n' "$1"; }
primary_deadline=999999999
tenancy_id=tenancy
ad=ad
drill_vcn_id=vcn
started=$SECONDS
if wait_oci_resource_ready "$RESOURCE_KIND" expected-id AVAILABLE "$MAX_SECONDS" test_wait expected-name; then rc=0; else rc=$?; fi
printf 'rc=%s elapsed=%s\n' "$rc" "$((SECONDS - started))"
'''

        def run(mode: str, max_seconds: int = 2) -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
                "MAX_SECONDS": str(max_seconds),
                "RESOURCE_KIND": "subnet" if mode == "wrong_vcn" else "boot-volume",
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            }
            return subprocess.run(
                ["bash", "-c", script], env=env, text=True, capture_output=True, timeout=max_seconds + 3, check=True
            )

        assert "rc=0" in run("available").stdout
        for mode in ("wrong_id", "wrong_name", "wrong_compartment", "wrong_ad", "wrong_vcn", "null"):
            result = run(mode)
            assert "rc=45" in result.stdout
            assert "state:test_wait_status=INVALID_GET_PAYLOAD" in result.stderr
        unknown = run("unknown")
        assert "rc=44" in unknown.stdout and "state:test_wait_status=UNKNOWN_STATE" in unknown.stderr
        terminal = run("terminal")
        assert "rc=44" in terminal.stdout and "state:test_wait_status=TERMINAL_FAULTY" in terminal.stderr
        injected = run("injected")
        assert "rc=44" in injected.stdout and "INJECTED" not in injected.stderr
        assert "rc=17" in run("auth").stdout
        assert "rc=86" in run("malformed").stdout
        slow = run("slow", 1)
        assert "rc=42" in slow.stdout and "elapsed=1" in slow.stdout


def test_restored_volume_hydration_wait_is_identity_source_size_and_time_bound() -> None:
    helper = WORKFLOW[
        WORKFLOW.index("wait_restored_boot_volume_hydrated() {") :
        WORKFLOW.index("wait_attached_vnic()", WORKFLOW.index("wait_restored_boot_volume_hydrated() {"))
    ]
    assert 'oci bv boot-volume get --boot-volume-id "$id"' in helper
    assert 'd.get("id")==os.environ["EXPECTED_ID"]' in helper
    assert 'source.get("id")==os.environ["EXPECTED_BACKUP_ID"]' in helper
    assert 'd.get("size-in-gbs")==97' in helper
    assert 'isinstance(hydrated,bool)' in helper
    assert 'state_set restored_volume_hydration_status TIMEOUT' in helper
    gate = WORKFLOW[
        WORKFLOW.index("mark_phase wait_restored_boot_volume_available") :
        WORKFLOW.index("runner_ip=", WORKFLOW.index("mark_phase wait_restored_boot_volume_available"))
    ]
    assert 'mark_phase wait_restored_boot_volume_hydrated' in gate
    assert 'restored_gate_deadline=$(( $(monotonic_now) + restored_gate_seconds ))' in gate
    assert 'restored_hydration_seconds="$(bounded_wait_seconds 3600 "$restored_gate_deadline")"' in gate
    assert 'wait_restored_boot_volume_hydrated "$restored_id" "$stamp-restore" "$backup_id" "$restored_hydration_seconds"' in gate
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "restored volume hydration" in receipt
    assert "restored_volume_hydration_failure_rc" in receipt

    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        oci_start = WORKFLOW.index("oci_json_request() {")
        oci_end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", oci_start)
        helpers = textwrap.dedent(WORKFLOW[oci_start:oci_end] + helper)
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
case "$FAKE_MODE" in
  ready) hydrated=true; source=backup; size=97; state=AVAILABLE ;;
  waiting) hydrated=false; source=backup; size=97; state=AVAILABLE ;;
  wrong_source) hydrated=true; source=other; size=97; state=AVAILABLE ;;
  wrong_size) hydrated=true; source=backup; size=96; state=AVAILABLE ;;
  terminal) hydrated=false; source=backup; size=97; state=FAULTY ;;
esac
printf '{"data":{"id":"restored","display-name":"restore-name","compartment-id":"tenancy","availability-domain":"ad","size-in-gbs":%s,"source-details":{"type":"bootVolumeBackup","id":"%s"},"lifecycle-state":"%s","is-hydrated":%s}}\n' "$size" "$source" "$state" "$hydrated"
"""
        )
        fake_oci.chmod(0o755)
        script = "set -e\n" + helpers + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
bounded_wait_seconds() { printf '%s\n' "$1"; }
primary_deadline=999999999
tenancy_id=tenancy
ad=ad
started=$SECONDS
if wait_restored_boot_volume_hydrated restored restore-name backup "$MAX_SECONDS"; then rc=0; else rc=$?; fi
printf 'rc=%s elapsed=%s\n' "$rc" "$((SECONDS - started))"
'''

        def run(mode: str, max_seconds: int = 2) -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
                "MAX_SECONDS": str(max_seconds),
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            }
            return subprocess.run(
                ["bash", "-c", script], env=env, text=True, capture_output=True, timeout=max_seconds + 3, check=True
            )

        assert "rc=0" in run("ready").stdout
        for mode in ("wrong_source", "wrong_size"):
            result = run(mode)
            assert "rc=45" in result.stdout
            assert "state:restored_volume_hydration_status=INVALID_GET_PAYLOAD" in result.stderr
        terminal = run("terminal")
        assert "rc=44" in terminal.stdout
        assert "state:restored_volume_hydration_status=TERMINAL_FAULTY" in terminal.stderr
        waiting = run("waiting", 1)
        assert "rc=42 elapsed=1" in waiting.stdout
        assert "state:restored_volume_hydration_status=TIMEOUT" in waiting.stderr


def test_discovery_and_cleanup_fail_closed_on_ambiguous_or_unproven_absence() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        oci_start = WORKFLOW.index("oci_json_request() {")
        oci_end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", oci_start)
        discovery_start = WORKFLOW.index("discover_named_id() {")
        discovery_end = WORKFLOW.index("delete_resource_once()", discovery_start)
        helpers = textwrap.dedent(WORKFLOW[oci_start:oci_end] + WORKFLOW[discovery_start:discovery_end])
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
case "$FAKE_MODE" in
  empty) echo '{"data":[]}' ;;
  terminated) echo '{"data":{"id":"expected-id","lifecycle-state":"TERMINATED"}}' ;;
  wrong_id) echo '{"data":{"id":"other","lifecycle-state":"TERMINATED"}}' ;;
  notfound) echo '{"code":"NotAuthorizedOrNotFound","status":404}' >&2; exit 17 ;;
  auth) echo '{"code":"NotAuthenticated","status":401}' >&2; exit 17 ;;
  timeout_then_terminated|timeout_then_notfound)
    count_file="$RUNNER_TEMP/$FAKE_MODE.count"
    count=0; [[ -f "$count_file" ]] && count="$(cat "$count_file")"
    count=$((count + 1)); printf '%s\n' "$count" > "$count_file"
    if (( count == 1 )); then exit 124; fi
    if [[ "$FAKE_MODE" == timeout_then_terminated ]]; then
      echo '{"data":{"id":"expected-id","lifecycle-state":"TERMINATED"}}'
    else
      echo '{"code":"NotAuthorizedOrNotFound","status":404}' >&2; exit 17
    fi ;;
  persistent_timeout) exit 124 ;;
esac
"""
        )
        fake_oci.chmod(0o755)
        base = "set -e\n" + helpers + r'''
bounded_wait_seconds() { printf '%s\n' "$1"; }
primary_deadline=999999999; cleanup_deadline=999999999
tenancy_id=tenancy; boot_id=boot; ad=ad; drill_vcn_id=vcn
'''

        def run(fragment: str, mode: str, timeout: int = 5) -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            }
            return subprocess.run(["bash", "-c", base + fragment], env=env, text=True, capture_output=True, timeout=timeout, check=True)

        discovered = run('if discover_named_id boot-volume missing 1; then rc=0; else rc=$?; fi; echo rc=$rc\n', "empty")
        assert "rc=3" in discovered.stdout
        assert "rc=0" in run('if wait_absent boot-volume expected-id 2; then rc=0; else rc=$?; fi; echo rc=$rc\n', "terminated").stdout
        assert "rc=0" in run('if wait_absent boot-volume expected-id 2; then rc=0; else rc=$?; fi; echo rc=$rc\n', "notfound").stdout
        assert "rc=0" in run('if wait_absent boot-volume expected-id 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "timeout_then_terminated").stdout
        assert "rc=0" in run('if wait_absent boot-volume expected-id 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "timeout_then_notfound").stdout
        assert "rc=1" in run('if wait_absent boot-volume expected-id 2; then rc=0; else rc=$?; fi; echo rc=$rc\n', "persistent_timeout").stdout
        assert "rc=1" in run('if wait_absent boot-volume expected-id 1; then rc=0; else rc=$?; fi; echo rc=$rc\n', "wrong_id").stdout
        assert "rc=1" in run('if wait_absent boot-volume expected-id 1; then rc=0; else rc=$?; fi; echo rc=$rc\n', "auth").stdout
        ids=" ".join(f"id-{index}" for index in range(21))
        cardinality = run(f'if wait_all_absent instance 2 {ids}; then rc=0; else rc=$?; fi; echo rc=$rc\n', "empty")
        assert "rc=94" in cardinality.stdout


def test_backup_wait_deadline_and_state_classification_are_adversarially_bounded() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        oci_start = WORKFLOW.index("oci_json_request() {")
        oci_end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", oci_start)
        wait_start = WORKFLOW.index("wait_backup_available() {")
        wait_end = WORKFLOW.index("select_fresh_operation_backups()", wait_start)
        helpers = textwrap.dedent(WORKFLOW[oci_start:oci_end] + WORKFLOW[wait_start:wait_end])
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
case "$FAKE_MODE" in
  slow) sleep 5; echo '{"data":{"id":"backup-id","lifecycle-state":"AVAILABLE"}}' ;;
  null) echo '{"data":{"id":"backup-id","lifecycle-state":null}}' ;;
  unknown) printf '%s\n' '{"data":{"id":"backup-id","lifecycle-state":"ALIEN\\nINJECTED"}}' ;;
  trailing) printf '%s\n' '{"data":{"id":"backup-id","lifecycle-state":"AVAILABLE\\n"}}' ;;
  carriage) printf '%s\n' '{"data":{"id":"backup-id","lifecycle-state":"CREATING\\r"}}' ;;
  terminal) echo '{"data":{"id":"backup-id","lifecycle-state":"FAULTY"}}' ;;
  available) echo '{"data":{"id":"backup-id","lifecycle-state":"AVAILABLE"}}' ;;
esac
"""
        )
        fake_oci.chmod(0o755)
        script = "set -e\n" + helpers + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
bounded_wait_seconds() { printf '%s\n' "$1"; }
primary_deadline=999999999
started=$SECONDS
if wait_backup_available backup-id "$MAX_SECONDS"; then rc=0; else rc=$?; fi
printf 'rc=%s elapsed=%s\n' "$rc" "$((SECONDS - started))"
'''

        def run(mode: str, max_seconds: int = 2) -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
                "MAX_SECONDS": str(max_seconds),
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            }
            return subprocess.run(
                ["bash", "-c", script], env=env, text=True, capture_output=True, timeout=max_seconds + 3, check=True
            )

        slow = run("slow", 1)
        assert "rc=42" in slow.stdout
        assert "elapsed=1" in slow.stdout
        assert "state:backup_wait_status=TIMEOUT" in slow.stderr
        invalid = run("null")
        assert "rc=43" in invalid.stdout
        assert "state:backup_wait_status=INVALID_GET_PAYLOAD" in invalid.stderr
        unknown = run("unknown")
        assert "rc=41" in unknown.stdout
        assert "state:backup_wait_status=UNKNOWN_STATE" in unknown.stderr
        assert "state:backup_wait_last_state=UNKNOWN" in unknown.stderr
        assert "INJECTED" not in unknown.stderr
        trailing = run("trailing")
        assert "rc=41" in trailing.stdout
        assert "state:backup_wait_last_state=UNKNOWN" in trailing.stderr
        carriage = run("carriage")
        assert "rc=41" in carriage.stdout
        assert "state:backup_wait_last_state=UNKNOWN" in carriage.stderr
        terminal = run("terminal")
        assert "rc=41" in terminal.stdout
        assert "state:backup_wait_status=TERMINAL_FAULTY" in terminal.stderr
        assert "rc=0" in run("available").stdout


def test_backup_selector_separates_expired_from_malformed_inventory() -> None:
    start = WORKFLOW.index("select_fresh_operation_backups()")
    end = WORKFLOW.index("superseded_operation_backups()", start)
    selector = WORKFLOW[start:end].replace("\n          ", "\n")
    now = datetime.datetime.now(datetime.timezone.utc)
    prefix = "issue-881-root-recovery-source"
    boot_id = "ocid1.bootvolume.source"

    def backup(identifier: str, age_hours: int) -> dict[str, object]:
        return {
            "id": identifier,
            "display-name": f"{prefix}-{identifier}",
            "boot-volume-id": boot_id,
            "lifecycle-state": "AVAILABLE",
            "type": "FULL",
            "size-in-gbs": 97,
            "time-created": (now - datetime.timedelta(hours=age_hours)).isoformat(),
        }

    script = selector + "\nprintf '%s' \"$PAYLOAD\" | select_fresh_operation_backups\n"
    env = os.environ | {"backup_prefix": prefix, "boot_id": boot_id}
    # Shell functions read these as shell variables rather than environment;
    # seed them explicitly without interpolating the JSON payload.
    script = f"backup_prefix={prefix!r}; boot_id={boot_id!r}\n" + script
    pending = backup("pending", 1)
    pending["lifecycle-state"] = "CREATING"
    payload = {"data": [backup("fresh", 1), backup("expired", 25), backup("future", -1), pending]}
    result = subprocess.run(["bash", "-c", script], env=env | {"PAYLOAD": json.dumps(payload)}, text=True, capture_output=True)
    assert result.returncode == 0
    selected = json.loads(result.stdout)
    assert selected["candidate_ids"] == ["fresh"]
    assert selected["ineligible_ids"] == ["expired"]
    assert selected["invalid_ids"] == ["future"]
    assert selected["blocking_ids"] == ["pending"]

    missing_data = subprocess.run(["bash", "-c", script], env=env | {"PAYLOAD": "{}"}, text=True, capture_output=True)
    assert missing_data.returncode != 0


def test_oci_json_request_handles_warning_transients_and_errors() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        _exercise_oci_json_request_adversarial_cases(Path(temp_dir))


def _exercise_oci_json_request_adversarial_cases(tmp_path: Path) -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    fake_oci = tmp_path / "fake-oci"
    fake_oci.write_text(
        """#!/usr/bin/env bash
set -u
count_file="$RUNNER_TEMP/fake-count"
case "$FAKE_MODE" in
  warning_valid)
    echo 'API key warning' >&2
    echo '{"data":{"lifecycle-state":"AVAILABLE"}}'
    ;;
  empty_then_valid)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    count=$((count + 1))
    printf '%s' "$count" >"$count_file"
    echo 'API key warning' >&2
    (( count < 2 )) || echo '{"data":{"lifecycle-state":"AVAILABLE"}}'
    ;;
  persistent_empty)
    echo 'API key warning' >&2
    ;;
  malformed)
    echo '{malformed'
    ;;
  auth)
    echo '{"code":"NotAuthenticated","status":401}' >&2
    exit 17
    ;;
  transport)
    echo 'connection reset' >&2
    exit 18
    ;;
esac
"""
    )
    fake_oci.chmod(0o755)

    script = """
bounded_wait_seconds() { printf '%s\\n' "$1"; }
primary_deadline=999999999
""" + helper + """
if oci_json_request "$FAKE_OCI"; then
  printf 'rc=0\\njson=%s\\nstderr=%s\\n' "$OCI_JSON_OUTPUT" "$OCI_JSON_ERROR"
else
  rc=$?
  printf 'rc=%s\\njson=%s\\nstderr=%s\\n' "$rc" "$OCI_JSON_OUTPUT" "$OCI_JSON_ERROR"
fi
"""

    def run(mode: str) -> str:
        env = os.environ | {
            "RUNNER_TEMP": str(tmp_path),
            "FAKE_OCI": str(fake_oci),
            "FAKE_MODE": mode,
            "OCI_JSON_MAX_ATTEMPTS": "3",
            "OCI_JSON_RETRY_DELAY_SECONDS": "0",
        }
        (tmp_path / "fake-count").unlink(missing_ok=True)
        result = subprocess.run(["bash", "-c", script], env=env, text=True, capture_output=True, check=True)
        return result.stdout

    warning = run("warning_valid")
    assert "rc=0" in warning and 'json={"data"' in warning and "stderr=API key warning" in warning
    transient = run("empty_then_valid")
    assert "rc=0" in transient and 'json={"data"' in transient
    assert "rc=86" in run("persistent_empty")
    assert "stderr=INVALID_JSON_SUCCESS_RESPONSE" in run("malformed")
    assert "rc=17" in run("auth") and "NotAuthenticated" in run("auth")
    assert "rc=18" in run("transport") and "connection reset" in run("transport")


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
    assert "assert 0 <= age < 86400" in block
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
    backup_filter = WORKFLOW[
        WORKFLOW.index("Preserve every exact-name backup ID") :
        WORKFLOW.index("boot_inventory=", WORKFLOW.index("Preserve every exact-name backup ID"))
    ]
    assert 'x.get("lifecycle-state")!="TERMINATED"' not in backup_filter
    assert "failed_run_backup_direct_get_states" in backup_filter
    assert "failed_run_backup_cleanup_status PROVEN_TERMINAL" in backup_filter
    assert "failed_run_backup_cleanup_status GET_FAILED" in backup_filter
    assert 'failed_backup_error="${failed_backup_error:-UNKNOWN_STATE}"' in backup_filter
    assert "failed_run_backup_cleanup_status MULTIPLE_EXACT_BACKUP_IDS" in backup_filter
    assert "failed_run_backup_available_ids" in backup_filter
    assert "if (( ${#ids[@]} > 1 ))" in backup_filter
    assert 'for id in "${ids[@]}"' in backup_filter
    classification_loop = backup_filter[
        backup_filter.index('for id in "${ids[@]}"') : backup_filter.index("done", backup_filter.index('for id in "${ids[@]}"'))
    ]
    assert "exit 91" not in classification_loop
    assert "exit 92" not in classification_loop
    assert "exit 93" not in classification_loop
    assert "failed_backup_state=GET_FAILED" in classification_loop
    assert "failed_backup_state=INVALID_RESPONSE" in classification_loop
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
