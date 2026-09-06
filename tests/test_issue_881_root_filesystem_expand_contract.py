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
PAID_GUARD = ROOT / "ops/oci_paid_acceptance_guard.py"
WATCHDOG = (ROOT / ".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()


def _paid_guard(shapes: dict, availability: dict, source_shape: str = "VM.Standard.E5.Flex") -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as temp_dir:
        shape_path = Path(temp_dir) / "shapes.json"
        availability_path = Path(temp_dir) / "availability.json"
        memory_availability_path = Path(temp_dir) / "memory-availability.json"
        shape_path.write_text(json.dumps(shapes))
        availability_path.write_text(json.dumps(availability))
        memory_availability_path.write_text(json.dumps({"data": {"available": 1}}))
        return subprocess.run(
            [
                "python",
                str(PAID_GUARD),
                "--shapes-json",
                str(shape_path),
                "--availability-json",
                str(availability_path),
                "--memory-availability-json",
                str(memory_availability_path),
                "--source-shape",
                source_shape,
                "--now-utc",
                "2026-09-05T05:00:00Z",
            ],
            text=True,
            capture_output=True,
        )


def _paid_shape(*, min_ocpus: float = 1, max_ocpus: float = 64,
                min_memory: float = 1, max_memory: float = 1024) -> dict:
    return {
        "data": [{
            "shape": "VM.Standard.E5.Flex",
            "ocpu-options": {"min": min_ocpus, "max": max_ocpus},
            "memory-options": {"min-in-g-bs": min_memory, "max-in-g-bs": max_memory},
        }]
    }


def test_paid_capacity_guard_is_bounded_and_fail_closed() -> None:
    approved = _paid_guard(_paid_shape(), {"data": {"available": 1}})
    assert approved.returncode == 0, approved.stderr
    payload = json.loads(approved.stdout)
    assert payload == payload | {
        "status": "APPROVED",
        "shape": "VM.Standard.E5.Flex",
        "ocpus": 1,
        "memory_gb": 1,
        "runtime_limit_seconds": 1800,
        "compute_budget_usd": "1.00",
        "hourly_rate_max_usd": "0.0265",
        "billing_class": "PAID_BOUNDED",
    }
    for shapes, availability, source, reason in (
        (_paid_shape(), {"data": {"available": 1}}, "VM.Standard.A1.Flex", "ARCHITECTURE_OR_SOURCE_SHAPE_MISMATCH"),
        (_paid_shape(), {"data": {"available": 0}}, "VM.Standard.E5.Flex", "INSUFFICIENT_SERVICE_LIMIT_HEADROOM"),
        ({"data": []}, {"data": {"available": 1}}, "VM.Standard.E5.Flex", "PAID_SHAPE_NOT_UNIQUELY_AVAILABLE"),
    ):
        rejected = _paid_guard(shapes, availability, source)
        assert rejected.returncode == 2
        assert json.loads(rejected.stdout) == {"status": "REJECTED", "reason": reason}


def test_paid_guard_uses_live_oci_memory_option_field_names() -> None:
    assert 'memory_options.get("min-in-g-bs")' in PAID_GUARD.read_text()
    assert 'memory_options.get("max-in-g-bs")' in PAID_GUARD.read_text()
    legacy = _paid_shape()
    options = legacy["data"][0]["memory-options"]
    options["min-in-gbs"] = options.pop("min-in-g-bs")
    options["max-in-gbs"] = options.pop("max-in-g-bs")
    rejected = _paid_guard(legacy, {"data": {"available": 1}})
    assert rejected.returncode == 2
    assert json.loads(rejected.stdout) == {"status": "REJECTED", "reason": "INVALID_MIN_MEMORY"}


def test_paid_fallback_requires_quota_absence_preflight_and_exact_caps() -> None:
    paid = WORKFLOW.index("mark_phase paid_capacity_preflight")
    launch = WORKFLOW.index("mark_phase launch_bounded_paid_acceptance_instance", paid)
    assert '[[ "$shape" == VM.Standard.E5.Flex ]] || exit 92' in WORKFLOW[:paid]
    assert 'create_json_once instance_create "$stamp-boot-acceptance"' not in WORKFLOW
    assert "oci compute shape list" in WORKFLOW[paid:]
    assert "oci limits resource-availability get" in WORKFLOW[paid:]
    assert WORKFLOW[paid:launch].count("python ops/oci_paid_acceptance_guard.py") == 2
    assert 'paid_deadline=$(( $(monotonic_now) + paid_values[7] ))' in WORKFLOW[paid:]
    assert "paid_instance_create" in WORKFLOW[paid:]
    restore = WORKFLOW.index("mark_phase create_restored_boot_volume")
    dispatch = WORKFLOW.index("mark_phase dispatch_paid_instance_watchdog")
    assert "issue-881-paid-instance-watchdog.yml/dispatches" in WORKFLOW
    assert dispatch < restore < paid < launch
    assert WORKFLOW.index("paid_watchdog_status ARMED_PRE_MUTATION") < restore
    assert WORKFLOW.index("paid_watchdog_status ARMED", paid) < launch
    assert 'select(.user.login=="github-actions[bot]")' in WORKFLOW[dispatch:restore]
    assert 'watchdog_run_id="$(WATCHDOG_COMMENTS=' in WORKFLOW[dispatch:restore]
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "paid hourly/runtime/budget caps:" in receipt
    assert "present_redacted" in receipt


def test_launch_boundary_refreshes_all_live_oci_constraints() -> None:
    boundary = WORKFLOW.index("# Refresh all mutable primary-source constraints")
    launch = WORKFLOW.index("mark_phase launch_bounded_paid_acceptance_instance", boundary)
    block = WORKFLOW[boundary:launch]
    assert "oci compute shape list" in block
    assert "standard-e4-core-count" in block
    assert "standard-e4-memory-count" in block
    assert block.index("standard-e4-memory-count") < block.index('paid_launch_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"')
    assert "python ops/oci_paid_acceptance_guard.py" in block
    assert 'actions/runs/${watchdog_run_id}' in block
    assert "d.get('status') in {'queued','in_progress'}" in block
    assert "paid_watchdog_status LIVE_AT_LAUNCH" in block
    assert "PAID_WATCHDOG_LAUNCH" in block


def test_independent_watchdog_retries_compute_and_storage_cleanup() -> None:
    assert "timeout-minutes: 300" in WATCHDOG
    assert "PAID_WATCHDOG_ARMED" in WATCHDOG
    assert "PAID_WATCHDOG_LAUNCH" in WATCHDOG
    assert WATCHDOG.count("oci compute instance list") >= 2
    assert "instance_discovery_deadline=$((SECONDS + 120))" in WATCHDOG
    assert "instance_confirmation_deadline=$((SECONDS + 120))" in WATCHDOG
    assert "late_instance_clean_count=$((late_instance_clean_count + 1))" in WATCHDOG
    assert "late_instance_clean_count >= 3" in WATCHDOG
    assert WATCHDOG.count("assert isinstance(data,list) and all(isinstance(x,dict) for x in data)") >= 4
    assert 'if (( ${#ids[@]} == 0 )); then' in WATCHDOG
    assert "instance_inventory_clean == 1" in WATCHDOG
    assert "A known exact ID can never be downgraded" in WATCHDOG
    assert "instance_get_error" in WATCHDOG
    instance_loop = WATCHDOG[WATCHDOG.index("while (( SECONDS < instance_cleanup_deadline ))"):]
    assert instance_loop.index("while (( SECONDS < instance_cleanup_deadline ))") < instance_loop.index("oci compute instance terminate")
    volume_loop = WATCHDOG[WATCHDOG.index("deadline=$((SECONDS + 120))"):]
    assert volume_loop.index("while (( SECONDS < deadline ))") < volume_loop.index("oci bv boot-volume delete")
    assert "oci bv boot-volume get" in volume_loop
    assert "volume_terminal_proven == 1" in volume_loop
    assert "launch_expiry <= now + 1800" in WATCHDOG
    assert "launch_expiry <= effective_expiry" not in WATCHDOG
    assert "launch_expiry < effective_expiry" in WATCHDOG
    assert 'effective_expiry="$now"' in WATCHDOG
    assert "if launch_comments=\"$(timeout --signal=KILL 30s gh api" in WATCHDOG
    assert "while ! timeout --signal=KILL 30s oci bv boot-volume list" in WATCHDOG
    assert "late_instance_id" in WATCHDOG and 'terminate --instance-id "$late_instance_id"' in WATCHDOG
    late_instance_loop = WATCHDOG[WATCHDOG.index('if [[ -n "$late_instance_id" ]]'):]
    assert '2>"$late_instance_get_error"' in late_instance_loop
    assert "status([^0-9]+)404" in late_instance_loop
    assert late_instance_loop.index("instance_terminal_proven=1; break") < late_instance_loop.index(
        "volume_discovery_deadline="
    )
    assert "late_volume_id" in WATCHDOG and 'delete --boot-volume-id "$late_volume_id"' in WATCHDOG
    assert "late_volume_deadline=$((SECONDS + 120))" in WATCHDOG
    assert "late_volume_clean_count >= 3" in WATCHDOG


def test_watchdog_destructive_entrypoint_is_owner_gated() -> None:
    job = WATCHDOG[WATCHDOG.index("terminate-exact-paid-instance:"):]
    assert "PARENT_RUN_ID" in job and "PARENT_RUN_ATTEMPT" in job
    assert "actions/runs/${PARENT_RUN_ID}" in job
    assert "d.get('event')=='issue_comment'" in job
    assert "d.get('status')=='in_progress'" in job
    assert "d.get('actor',{}).get('login')=='olegmed1-art'" in job
    assert "d.get('triggering_actor',{}).get('login')=='olegmed1-art'" in job
    assert "GITHUB_ACTOR\" == 'github-actions[bot]'" in job
    assert "hmac.compare_digest" in job and "AUTHORIZATION_HMAC" in job
    assert '-f "inputs[parent_run_id]=${GITHUB_RUN_ID}"' in WORKFLOW
    assert '-f "inputs[parent_run_attempt]=${GITHUB_RUN_ATTEMPT}"' in WORKFLOW
    assert '-f "inputs[authorization_hmac]=$watchdog_authorization_hmac"' in WORKFLOW
    assert "hmac.new" in WORKFLOW


def test_watchdog_propagates_all_inventory_validator_failures() -> None:
    assert "< <(STAMP=" not in WATCHDOG
    for name in ("instance-ids", "late-instance-ids", "volume-ids", "late-volume-ids"):
        assert f'$RUNNER_TEMP/{name}' in WATCHDOG
    assert WATCHDOG.count("then return 101; fi") == 2
    assert WATCHDOG.count("then exit 102; fi") == 2
    assert WATCHDOG.count("sys.stdout.write('\\n'.join(matched)+('\\n' if matched else ''))") == 4
    assert "print('\\n'.join(matched))" not in WATCHDOG


def test_paid_price_basis_expires_fail_closed() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        shape_path = Path(temp_dir) / "shapes.json"
        cpu_path = Path(temp_dir) / "cpu.json"
        memory_path = Path(temp_dir) / "memory.json"
        shape_path.write_text(json.dumps(_paid_shape()))
        cpu_path.write_text(json.dumps({"data": {"available": 1}}))
        memory_path.write_text(json.dumps({"data": {"available": 1}}))
        result = subprocess.run(
            ["python", str(PAID_GUARD), "--shapes-json", str(shape_path),
             "--availability-json", str(cpu_path), "--memory-availability-json", str(memory_path),
             "--source-shape", "VM.Standard.E5.Flex", "--now-utc", "2026-10-05T00:00:00Z"],
            text=True, capture_output=True,
        )
        assert result.returncode == 2
        assert json.loads(result.stdout)["reason"] == "PRICE_BASIS_EXPIRED"


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
    assert '"/oracle-ops issue-881-expand-root-and-recover-bba508-paid-bounded"' in WORKFLOW
    assert '"/oracle-ops issue-881-reconcile-run-34013072946"' in WORKFLOW


def test_owner_command_is_case_sensitive_before_any_oci_or_host_access() -> None:
    block = WORKFLOW[
        WORKFLOW.index("Create fresh full backup and prove isolated restored-root boot acceptance") :
        WORKFLOW.index("monotonic_now() { awk")
    ]
    assert '[[ "$OWNER_COMMAND" != "$RECOVERY_COMMAND" && "$OWNER_COMMAND" != "$CLEANUP_ONLY_COMMAND" ]]' in block
    assert "UV_ROOT_OWNER_COMMAND_REJECTED" in block
    gate = r'''
RECOVERY_COMMAND=/oracle-ops\ issue-881-expand-root-and-recover-bba508-paid-bounded
CLEANUP_ONLY_COMMAND=/oracle-ops\ issue-881-reconcile-run-34013072946
if [[ "$OWNER_COMMAND" != "$RECOVERY_COMMAND" && "$OWNER_COMMAND" != "$CLEANUP_ONLY_COMMAND" ]]; then exit 23; fi
'''
    for command, expected_rc in (
        ("/oracle-ops issue-881-expand-root-and-recover-bba508-paid-bounded", 0),
        ("/oracle-ops issue-881-reconcile-run-34013072946", 0),
        ("/ORACLE-OPS issue-881-reconcile-run-34013072946", 23),
        ("/oracle-ops issue-881-reconcile-run-34013072946 ", 23),
    ):
        result = subprocess.run(["bash", "-c", gate], env=os.environ | {"OWNER_COMMAND": command})
        assert result.returncode == expected_rc
    assert WORKFLOW.index("UV_ROOT_OWNER_COMMAND_REJECTED") < WORKFLOW.index("for value in OCI_USER")
    assert WORKFLOW.index("UV_ROOT_OWNER_COMMAND_REJECTED") < WORKFLOW.index("oci_json_request oci iam")


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
    backup_step = WORKFLOW[
        WORKFLOW.index("Create fresh full backup and prove isolated restored-root boot acceptance") :
        WORKFLOW.index("Resolve pinned SSH identity")
    ]
    assert "PRIMARY_DEADLINE: ${{ steps.budget.outputs.primary_deadline }}" in backup_step
    assert "CLEANUP_DEADLINE: ${{ steps.budget.outputs.cleanup_deadline }}" in backup_step
    assert "primary_deadline='${{ steps.budget.outputs.primary_deadline }}'" not in backup_step
    assert "cleanup_deadline='${{ steps.budget.outputs.cleanup_deadline }}'" not in backup_step
    assert 14400 + 2400 + 2400 + 1500 + 900 == 360 * 60

    # Worst-case current-attempt cleanup: eight 30s exact-name rediscoveries,
    # eight 20s delete requests, a 10m instance wait, and seven 2m dependent
    # resource/backup waits. This fits the executable 40m cleanup reserve.
    cleanup_worst_case_seconds = 8 * 30 + 8 * 20 + 600 + 7 * 120
    assert cleanup_worst_case_seconds == 1840
    conservative_with_superseded = cleanup_worst_case_seconds + 20 * 20 + 120
    assert conservative_with_superseded == 2360
    assert conservative_with_superseded < 16800 - 14400
    for exact in (
        'discover_named_id boot-volume "$stamp-restore" 30 "$cleanup_deadline"',
        'discover_named_id vcn "$stamp-vcn" 30 "$cleanup_deadline"',
        'discover_named_id instance "$stamp-boot-acceptance" 30 "$cleanup_deadline"',
        'discover_named_id backup "$backup_name" 30 "$cleanup_deadline"',
    ):
        assert exact in WORKFLOW
    assert "if (( prior_count > 20 ))" in WORKFLOW
    assert 'reconcile_bound_resource instance boot-acceptance 600' in WORKFLOW
    assert 'reconcile_bound_resource boot-volume restore 120' in WORKFLOW
    assert 'delete_total_seconds="$(bounded_wait_seconds 20 "$cleanup_deadline")"' in WORKFLOW
    assert 'timeout --signal=KILL "${delete_request_seconds}s" "${command[@]}"' in WORKFLOW
    assert 'if [[ "$rc" == 124 || "$rc" == 137 ]]' in WORKFLOW
    assert 'if (( ${#ids[@]} > 20 ))' in WORKFLOW
    assert 'superseded_backup_cleanup_status CARDINALITY_EXCEEDED' in WORKFLOW
    assert "superseded backup cleanup:" in WORKFLOW
    accepted = WORKFLOW.index("backup_accepted=1")
    retained_before_retirement = WORKFLOW.index('state_set retained_backup_id "$backup_id"', accepted)
    cardinality_exit = WORKFLOW.index('superseded_backup_cleanup_status CARDINALITY_EXCEEDED', accepted)
    assert accepted < retained_before_retirement < cardinality_exit
    assert 'state_set retained_backup_status ACCEPTED_ISOLATED_BOOT' in WORKFLOW[accepted:cardinality_exit]
    assert "retained_backup_status" in WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]

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
    assert "2,400-second cleanup" in WORKFLOW
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


def test_targeted_cleanup_rejects_near_match_resource_names() -> None:
    start = WORKFLOW.index("prior_named_ids() {")
    end = WORKFLOW.index("load_authoritative_failed_receipt()", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    target = "issue-881-root-recovery-source-run-33914733788-a1"
    payload = {
        "data": [
            {
                "id": "exact-id",
                "display-name": target + "-boot-acceptance",
                "lifecycle-state": "RUNNING",
            },
            {
                "id": "near-id",
                "display-name": target + "-extra-boot-acceptance",
                "lifecycle-state": "RUNNING",
            },
        ]
    }
    script = helper + r'''
oci_json_request() { OCI_JSON_OUTPUT="$PAYLOAD"; }
tenancy_id=tenancy; ad=ad; prior_prefix=issue-881-root-recovery-source-run-
stamp=issue-881-root-recovery-source-run-current-a1
target_prior_stamp=issue-881-root-recovery-source-run-33914733788-a1
prior_named_ids instance boot-acceptance
'''
    result = subprocess.run(
        ["bash", "-c", script],
        env=os.environ | {"PAYLOAD": json.dumps(payload)},
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "exact-id"


def test_repeated_inventory_ignores_only_valid_terminal_records() -> None:
    start = WORKFLOW.index("prior_named_ids() {")
    end = WORKFLOW.index("load_authoritative_failed_receipt()", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    target = "issue-881-root-recovery-source-run-33914733788-a1"
    payload = {
        "data": [
            {"id": "terminal-id", "display-name": target + "-boot-acceptance", "lifecycle-state": "TERMINATED"},
            {"id": "active-id", "display-name": target + "-boot-acceptance", "lifecycle-state": "RUNNING"},
        ]
    }
    setup = r'''
oci_json_request() { OCI_JSON_OUTPUT="$PAYLOAD"; }
tenancy_id=tenancy; ad=ad; prior_prefix=issue-881-root-recovery-source-run-
stamp=issue-881-root-recovery-source-run-current-a1
target_prior_stamp=issue-881-root-recovery-source-run-33914733788-a1
'''
    script = helper + setup + r'''
echo include="$(prior_named_ids instance boot-acceptance | paste -sd, -)"
echo active="$(prior_named_ids instance boot-acceptance active | paste -sd, -)"
'''
    result = subprocess.run(
        ["bash", "-c", script], env=os.environ | {"PAYLOAD": json.dumps(payload)}, text=True,
        capture_output=True, check=True,
    )
    assert "include=active-id,terminal-id" in result.stdout
    assert "active=active-id" in result.stdout

    payload["data"][0]["lifecycle-state"] = "ALIEN"
    rejected = subprocess.run(
        ["bash", "-c", helper + setup + "prior_named_ids instance boot-acceptance active\n"],
        env=os.environ | {"PAYLOAD": json.dumps(payload)}, text=True,
        capture_output=True,
    )
    assert rejected.returncode != 0


def test_predelete_validation_derives_normal_names_and_rejects_unknown_lifecycle() -> None:
    start = WORKFLOW.index("validate_bound_id_before_delete()")
    end = WORKFLOW.index("wait_instance_terminated()", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    setup = r'''
oci_json_request() { OCI_JSON_OUTPUT="$PAYLOAD"; return 0; }
cleanup_deadline=999999; tenancy_id=tenancy; ad=ad
prior_prefix=issue-881-root-recovery-source-run-
stamp=issue-881-root-recovery-source-run-current-a1
target_prior_stamp=
'''
    name = "issue-881-root-recovery-source-run-older-a2-boot-acceptance"

    def run(state: object, display_name: str = name) -> subprocess.CompletedProcess[str]:
        payload = {"data": {"id": "expected-id", "display-name": display_name,
                            "compartment-id": "tenancy", "availability-domain": "ad",
                            "lifecycle-state": state}}
        return subprocess.run(
            ["bash", "-c", helper + setup + r'''
if validate_bound_id_before_delete instance boot-acceptance expected-id; then
  echo "rc=0 name=$BOUND_EXPECTED_NAME delete=$BOUND_DELETE_REQUIRED"
else
  echo "rc=$?"
fi
'''],
            env=os.environ | {"PAYLOAD": json.dumps(payload)}, text=True, capture_output=True, check=True,
        )

    active = run("RUNNING")
    assert f"rc=0 name={name} delete=1" in active.stdout
    terminal = run("TERMINATED")
    assert f"rc=0 name={name} delete=0" in terminal.stdout
    assert "rc=97" in run(None).stdout
    assert "rc=97" in run("ALIEN").stdout
    assert "rc=97" in run("RUNNING", "issue-881-root-recovery-source-run-current-a1-boot-acceptance").stdout


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
        block.index("cleanup() {") : block.index("\n          record_reconcile_failure() {\n")
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
    assert "retained_backup_status" in receipt
    assert "last exact-name discovery:" in receipt
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
        "paid_instance_create",
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
    assert helper.count('bounded_retry_seconds="$(bounded_wait_seconds "$((retry_delay_seconds + 1))" "$absolute_deadline")"') == 2
    assert helper.count('sleep "$retry_delay_seconds"') == 2
    assert "return 86" in helper
    assert "if (( rc != 0 )); then" in helper
    assert "Retry only failures that are safe to classify as transient" in helper
    assert "return \"$rc\"" in helper
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
        assert "rc=42" in slow.stdout
        elapsed = int(slow.stdout.split("elapsed=", 1)[1].split()[0])
        assert 1 <= elapsed <= 2


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
        assert "rc=42" in waiting.stdout
        elapsed = int(waiting.stdout.split("elapsed=", 1)[1].split()[0])
        assert 1 <= elapsed <= 2
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
  timeout_then_found|killed_then_found)
    count_file="$RUNNER_TEMP/$FAKE_MODE.count"
    count=0; [[ -f "$count_file" ]] && count="$(cat "$count_file")"
    count=$((count + 1)); printf '%s\n' "$count" > "$count_file"
    if (( count == 1 )); then [[ "$FAKE_MODE" == timeout_then_found ]] && exit 124 || exit 137; fi
    echo '{"data":[{"id":"found-id","display-name":"expected-name","lifecycle-state":"AVAILABLE"}]}' ;;
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
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
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
        for transient in ("timeout_then_found", "killed_then_found"):
            retried_discovery = run(
                'if id="$(discover_named_id boot-volume expected-name 3)"; then rc=0; else rc=$?; fi; echo rc=$rc id=$id\n',
                transient,
            )
            assert "rc=0 id=found-id" in retried_discovery.stdout
        persistent_discovery = run(
            'if discover_named_id boot-volume expected-name 2; then rc=0; else rc=$?; fi; echo rc=$rc\n',
            "persistent_timeout",
        )
        assert "rc=42" in persistent_discovery.stdout
        assert "state:last_discovery_status=REQUEST_TIMEOUT_EXHAUSTED" in persistent_discovery.stderr
        failed_discovery = run(
            'if discover_named_id boot-volume expected-name 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "auth"
        )
        assert "rc=17" in failed_discovery.stdout
        assert "state:last_discovery_status=REQUEST_FAILED" in failed_discovery.stderr
        invalid_discovery = run(
            'if discover_named_id boot-volume expected-name 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "wrong_id"
        )
        assert "rc=45" in invalid_discovery.stdout
        assert "state:last_discovery_status=INVALID_RESPONSE" in invalid_discovery.stderr
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


def test_instance_rediscovery_uses_broad_inventory_and_strict_client_identity() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        oci_start = WORKFLOW.index("oci_json_request() {")
        oci_end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", oci_start)
        diagnostics_start = WORKFLOW.index("oci_response_shape() {")
        diagnostics_end = WORKFLOW.index("mark_phase() {", diagnostics_start)
        discovery_start = WORKFLOW.index("discover_named_id() {")
        discovery_end = WORKFLOW.index("wait_absent()", discovery_start)
        helpers = (
            textwrap.dedent("          " + WORKFLOW[oci_start:oci_end])
            + textwrap.dedent("          " + WORKFLOW[diagnostics_start:diagnostics_end])
            + textwrap.dedent("          " + WORKFLOW[discovery_start:discovery_end])
        )
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
printf '%s\\n' "$*" > "$RUNNER_TEMP/instance-list.args"
case "$FAKE_MODE" in
  exact)
    echo '{"data":[{"id":"near","display-name":"expected-name-extra","compartment-id":"tenancy","availability-domain":"ad","lifecycle-state":"RUNNING"},{"id":"exact-id","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"ad","lifecycle-state":"PROVISIONING"}]}' ;;
  wrong_compartment)
    echo '{"data":[{"id":"wrong","display-name":"expected-name","compartment-id":"other","availability-domain":"ad","lifecycle-state":"RUNNING"}]}' ;;
  wrong_ad)
    echo '{"data":[{"id":"wrong","display-name":"expected-name","compartment-id":"tenancy","availability-domain":"other-ad","lifecycle-state":"RUNNING"}]}' ;;
esac
"""
        )
        fake_oci.chmod(0o755)
        script = "set -e\n" + helpers + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
bounded_wait_seconds() { [[ "${BUDGET_MODE:-ok}" == fail ]] && return 42; printf '%s\n' "$1"; }
primary_deadline=999999999; tenancy_id=tenancy; boot_id=boot; ad=ad; drill_vcn_id=vcn
if id="$(discover_named_id instance expected-name 2)"; then rc=0; else rc=$?; fi
printf 'rc=%s id=%s\n' "$rc" "$id"
'''

        def run(mode: str, budget_mode: str = "ok") -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
                "BUDGET_MODE": budget_mode,
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            }
            return subprocess.run(
                ["bash", "-c", script], env=env, text=True, capture_output=True, timeout=5, check=True
            )

        exact = run("exact")
        assert "rc=0 id=exact-id" in exact.stdout
        args = (tmp_path / "instance-list.args").read_text()
        assert "compute instance list" in args
        assert "--display-name" not in args
        for mode in ("wrong_compartment", "wrong_ad"):
            invalid = run(mode)
            assert "rc=45 id=" in invalid.stdout
            assert "state:instance_discovery_status=INVALID_RESPONSE" in invalid.stderr
        budget = run("exact", "fail")
        assert "rc=42 id=" in budget.stdout
        assert "state:instance_discovery_status=BUDGET_EXHAUSTED" in budget.stderr
        assert "state:instance_discovery_failure_rc=42" in budget.stderr
        assert "state:instance_discovery_stderr_class=BUDGET_EXHAUSTED" in budget.stderr
        # A discovered instance is not trusted for SSH until the exact restored
        # boot volume is proven by the scoped attachment inventory.
        assert 'EXPECTED_INSTANCE_ID="$drill_instance_id" EXPECTED_BOOT_ID="$restored_id"' in WORKFLOW
        assert 'x.get("boot-volume-id")==os.environ["EXPECTED_BOOT_ID"]' in WORKFLOW


def test_oci_diagnostics_are_sanitized_and_classify_capacity_and_quota() -> None:
    start = WORKFLOW.index("oci_response_shape() {")
    end = WORKFLOW.index("mark_phase() {", start)
    # The slice begins after YAML block indentation; pad its first line before
    # dedenting so the embedded Python receives the same indentation as Actions.
    helpers = textwrap.dedent("          " + WORKFLOW[start:end])
    script = helpers + r'''
state_set() { printf '%s=%s\n' "$1" "$2"; }
OCI_JSON_OUTPUT="$TEST_STDOUT"
OCI_JSON_RAW_ERROR="$TEST_STDERR"
record_oci_diagnostic instance_create "$TEST_RC"
'''

    def run(stderr: str, rc: int = 1, stdout: str = "") -> str:
        result = subprocess.run(
            ["bash", "-c", script],
            env=os.environ | {"TEST_STDERR": stderr, "TEST_STDOUT": stdout, "TEST_RC": str(rc)},
            text=True,
            capture_output=True,
            check=True,
        )
        return result.stdout

    capacity = run("ServiceError: Out of capacity for shape VM.Standard.E5.Flex SECRET_TOKEN")
    assert "instance_create_stderr_class=CAPACITY_UNAVAILABLE" in capacity
    assert "SECRET_TOKEN" not in capacity
    quota = run('{"code":"LimitExceeded","message":"Service limit reached","status":400}')
    assert "instance_create_stderr_class=QUOTA_OR_SERVICE_LIMIT" in quota
    assert "instance_create_stdout_shape=EMPTY" in quota
    timeout = run("", 124, '{"data":{}}')
    assert "instance_create_stderr_class=REQUEST_TIMEOUT" in timeout
    assert "instance_create_stdout_shape=JSON_OBJECT" in timeout
    warning = run("benign CLI warning", 0, '{"data":{}}')
    assert "instance_create_stderr_class=NONE" in warning
    assert "OCI_JSON_RAW_ERROR=\"$OCI_JSON_ERROR\"" in WORKFLOW
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "isolated paid instance create diagnostic" in receipt
    assert "isolated paid instance launch discovery" in receipt
    assert "paid_instance_launch_discovery_stderr_class" in receipt
    assert "paid_instance_create_stderr_class" in receipt
    assert 'discover_named_id instance "$stamp-boot-acceptance" 90 "$primary_deadline" paid_instance_launch_discovery' in WORKFLOW


def test_oci_helper_clears_diagnostics_before_budget_failure() -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent("          " + WORKFLOW[start:end])
    script = helper + r'''
bounded_wait_seconds() { return 42; }
primary_deadline=0
OCI_JSON_OUTPUT=STALE_STDOUT
OCI_JSON_ERROR=STALE_ERROR
OCI_JSON_RAW_ERROR=STALE_SECRET
if oci_json_request should-not-run; then rc=0; else rc=$?; fi
printf 'rc=%s stdout=%s error=%s raw=%s\n' "$rc" "$OCI_JSON_OUTPUT" "$OCI_JSON_ERROR" "$OCI_JSON_RAW_ERROR"
'''
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "rc=42 stdout= error= raw="
    assert "STALE_SECRET" not in result.stdout
    diagnostics_start = WORKFLOW.index("oci_response_shape() {")
    diagnostics_end = WORKFLOW.index("mark_phase() {", diagnostics_start)
    diagnostics = textwrap.dedent("          " + WORKFLOW[diagnostics_start:diagnostics_end])
    classified = subprocess.run(
        ["bash", "-c", diagnostics + "printf '' | oci_error_class 42"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert classified.stdout.strip() == "BUDGET_EXHAUSTED"


def test_vnic_attachment_and_public_ip_retry_only_request_timeouts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        oci_start = WORKFLOW.index("oci_json_request() {")
        oci_end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", oci_start)
        wait_start = WORKFLOW.index("wait_attached_vnic() {")
        wait_end = WORKFLOW.index("select_fresh_operation_backups()", wait_start)
        helpers = textwrap.dedent(WORKFLOW[oci_start:oci_end] + WORKFLOW[wait_start:wait_end])
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
count_file="$RUNNER_TEMP/vnic-$FAKE_MODE.count"
count=0; [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
count=$((count + 1)); printf '%s\n' "$count" > "$count_file"
case "$FAKE_MODE" in
  timeout_then_attached)
    (( count == 1 )) && exit 124
    echo '{"data":[{"instance-id":"instance","lifecycle-state":"ATTACHED","vnic-id":"vnic"}]}' ;;
  timeout_then_public_ip)
    (( count == 1 )) && exit 137
    echo '{"data":{"id":"vnic","subnet-id":"subnet","public-ip":"203.0.113.7"}}' ;;
  persistent_timeout) exit 124 ;;
  auth) echo '{"code":"NotAuthenticated","status":401}' >&2; exit 17 ;;
  multiple) echo '{"data":[{"instance-id":"instance","lifecycle-state":"ATTACHED","vnic-id":"vnic-1"},{"instance-id":"instance","lifecycle-state":"ATTACHED","vnic-id":"vnic-2"}]}' ;;
  malformed) echo '{"data":null}' ;;
  invalid_public_ip) echo '{"data":{"id":"vnic","subnet-id":"subnet","public-ip":"not-an-ip"}}' ;;
esac
"""
        )
        fake_oci.chmod(0o755)
        base = "set -e\n" + helpers + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
bounded_wait_seconds() { printf '%s\n' "$1"; }
primary_deadline=999999999; tenancy_id=tenancy; drill_subnet_id=subnet
'''

        def run(fragment: str, mode: str, timeout: int = 7) -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            }
            return subprocess.run(
                ["bash", "-c", base + fragment], env=env, text=True, capture_output=True, timeout=timeout, check=True
            )

        attached = run(
            'if wait_attached_vnic instance 3; then rc=0; else rc=$?; fi; echo rc=$rc id=$ATTACHED_VNIC_ID\n',
            "timeout_then_attached",
        )
        assert "rc=0 id=vnic" in attached.stdout
        assert [line for line in attached.stderr.splitlines() if line.startswith("state:vnic_attachment_failure_rc=")][-1] == (
            "state:vnic_attachment_failure_rc=none"
        )
        public_ip = run(
            'if wait_vnic_public_ipv4 vnic 3; then rc=0; else rc=$?; fi; echo rc=$rc ip=$VNIC_PUBLIC_IPV4\n',
            "timeout_then_public_ip",
        )
        assert "rc=0 ip=203.0.113.7" in public_ip.stdout
        assert [line for line in public_ip.stderr.splitlines() if line.startswith("state:vnic_public_ip_failure_rc=")][-1] == (
            "state:vnic_public_ip_failure_rc=none"
        )
        persistent = run(
            'if wait_attached_vnic instance 2; then rc=0; else rc=$?; fi; echo rc=$rc\n',
            "persistent_timeout",
        )
        assert "rc=42" in persistent.stdout
        assert "state:vnic_attachment_status=TIMEOUT" in persistent.stderr
        auth = run('if wait_attached_vnic instance 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "auth")
        assert "rc=17" in auth.stdout
        assert (tmp_path / "vnic-auth.count").read_text().strip() == "1"
        ambiguous = run('if wait_attached_vnic instance 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "multiple")
        assert "rc=44" in ambiguous.stdout
        assert "state:vnic_attachment_failure_rc=44" in ambiguous.stderr
        malformed = run('if wait_attached_vnic instance 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "malformed")
        assert "rc=45" in malformed.stdout
        assert "state:vnic_attachment_failure_rc=45" in malformed.stderr
        invalid_ip = run('if wait_vnic_public_ipv4 vnic 3; then rc=0; else rc=$?; fi; echo rc=$rc\n', "invalid_public_ip")
        assert "rc=45" in invalid_ip.stdout
        assert "state:vnic_public_ip_failure_rc=45" in invalid_ip.stderr


def test_delete_retries_only_request_timeouts_within_aggregate_cleanup_budget() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        tmp_path = Path(temp_dir)
        start = WORKFLOW.index("delete_resource_once() {")
        end = WORKFLOW.index("create_json_once()", start)
        helper = textwrap.dedent(WORKFLOW[start:end])
        fake_oci = tmp_path / "oci"
        fake_oci.write_text(
            """#!/usr/bin/env bash
count_file="$RUNNER_TEMP/delete-$FAKE_MODE.count"
count=0; [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
count=$((count + 1)); printf '%s\n' "$count" > "$count_file"
case "$FAKE_MODE" in
  timeout_then_success) (( count == 1 )) && exit 124; exit 0 ;;
  killed_then_notfound) (( count == 1 )) && exit 137; echo '{"code":"NotAuthorizedOrNotFound","status":404}' >&2; exit 17 ;;
  persistent_timeout) exit 124 ;;
  auth) echo '{"code":"NotAuthenticated","status":401}' >&2; exit 17 ;;
esac
"""
        )
        fake_oci.chmod(0o755)
        script = "set -e\n" + helper + r'''
bounded_wait_seconds() { printf '4\n'; }
cleanup_deadline=999999999
if delete_resource_once boot-volume expected-id; then rc=0; else rc=$?; fi
count="$(cat "$RUNNER_TEMP/delete-$FAKE_MODE.count")"
printf 'rc=%s count=%s\n' "$rc" "$count"
'''

        def run(mode: str, timeout: int = 5) -> subprocess.CompletedProcess[str]:
            env = os.environ | {
                "RUNNER_TEMP": str(tmp_path),
                "PATH": f"{tmp_path}:{os.environ['PATH']}",
                "FAKE_MODE": mode,
            }
            return subprocess.run(
                ["bash", "-c", script], env=env, text=True, capture_output=True, timeout=timeout, check=True
            )

        assert "rc=0 count=2" in run("timeout_then_success").stdout
        assert "rc=0 count=2" in run("killed_then_notfound").stdout
        persistent = run("persistent_timeout")
        assert "rc=42" in persistent.stdout
        assert "UV_ROOT_DELETE_REQUEST_TIMEOUT" in persistent.stderr
        auth = run("auth")
        assert "rc=17 count=1" in auth.stdout
        assert "UV_ROOT_DELETE_REQUEST_NONZERO" in auth.stderr


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
        # Bash SECONDS is a coarse wall-clock counter and can cross two integer
        # boundaries during a one-second timeout on a loaded CI runner.
        elapsed = int(slow.stdout.split("elapsed=", 1)[1].split()[0])
        assert 1 <= elapsed <= 2
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
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo '{"code":"NotAuthenticated","status":401,"message":"configured maximum is 500 items"}' >&2
    exit 17
    ;;
  transport)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo 'connection reset' >&2
    exit 18
    ;;
  structured_transient)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo '{"code":"ServiceUnavailable","status":503}' >&2
    exit 19
    ;;
  prefixed_permanent)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo 'ServiceError: {"code":"InvalidParameter","status":400,"message":"request timed out"}' >&2
    exit 20
    ;;
  prefixed_transient)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo 'ServiceError: {"code":"UnexpectedGatewayError","status":502}' >&2
    exit 21
    ;;
  mixed_permanent)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo 'ServiceError: {"code":"InvalidParameter","status":503}' >&2
    exit 22
    ;;
  throttled)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    printf '%s' "$((count + 1))" >"$count_file"
    echo '{"code":"TooManyRequests","status":429}' >&2
    exit 23
    ;;
  transient_then_valid)
    count=0
    [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"
    count=$((count + 1))
    printf '%s' "$count" >"$count_file"
    if (( count < 2 )); then echo 'connection reset' >&2; exit 18; fi
    echo '{"data":{"lifecycle-state":"AVAILABLE"}}'
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
    auth = run("auth")
    assert "rc=17" in auth and "NotAuthenticated" in auth
    assert (tmp_path / "fake-count").read_text() == "1"
    transport = run("transport")
    assert "rc=18" in transport and "connection reset" in transport
    assert (tmp_path / "fake-count").read_text() == "3"
    structured_transient = run("structured_transient")
    assert "rc=19" in structured_transient and "ServiceUnavailable" in structured_transient
    assert (tmp_path / "fake-count").read_text() == "3"
    prefixed_permanent = run("prefixed_permanent")
    assert "rc=20" in prefixed_permanent and "InvalidParameter" in prefixed_permanent
    assert (tmp_path / "fake-count").read_text() == "1"
    prefixed_transient = run("prefixed_transient")
    assert "rc=21" in prefixed_transient and "UnexpectedGatewayError" in prefixed_transient
    assert (tmp_path / "fake-count").read_text() == "3"
    mixed_permanent = run("mixed_permanent")
    assert "rc=22" in mixed_permanent and "InvalidParameter" in mixed_permanent
    assert (tmp_path / "fake-count").read_text() == "1"
    throttled = run("throttled")
    assert "rc=23" in throttled and "TooManyRequests" in throttled
    assert (tmp_path / "fake-count").read_text() == "3"
    assert "rc=0" in run("transient_then_valid")


def test_prior_inventory_preserves_failure_diagnostic_and_original_rc() -> None:
    start = WORKFLOW.index("prior_named_ids() {")
    end = WORKFLOW.index("load_authoritative_failed_receipt()", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    harness = helper + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
record_oci_diagnostic() { printf 'diagnostic:%s:rc=%s\n' "$1" "$2" >&2; }
oci_json_request() { OCI_JSON_OUTPUT=""; OCI_JSON_ERROR='NotAuthenticated'; OCI_JSON_RAW_ERROR="$OCI_JSON_ERROR"; return 17; }
tenancy_id=tenancy; ad=ad; prior_prefix=prefix; stamp=current; target_prior_stamp=
if prior_named_ids instance boot-acceptance; then echo rc=0; else echo rc=$?; fi
'''
    result = subprocess.run(["bash", "-c", harness], text=True, capture_output=True, check=True)
    assert "rc=17" in result.stdout
    assert "state:prior_inventory_instance_status=REQUEST_FAILED" in result.stderr
    assert "state:prior_inventory_instance_failure_rc=17" in result.stderr
    assert "state:prior_inventory_last_resource=instance" in result.stderr
    assert "state:prior_inventory_last_status=REQUEST_FAILED" in result.stderr
    assert "state:prior_inventory_last_failure_rc=17" in result.stderr
    assert "diagnostic:prior_inventory_instance:rc=17" in result.stderr

    invalid = helper + r'''
state_set() { printf 'state:%s=%s\n' "$1" "$2" >&2; }
record_oci_diagnostic() { printf 'diagnostic:%s:rc=%s\n' "$1" "$2" >&2; }
oci_json_request() { OCI_JSON_OUTPUT='{"data":{}}'; OCI_JSON_ERROR=''; OCI_JSON_RAW_ERROR=''; return 0; }
tenancy_id=tenancy; ad=ad; prior_prefix=prefix; stamp=current; target_prior_stamp=
if prior_named_ids instance boot-acceptance; then echo rc=0; else echo rc=$?; fi
'''
    invalid_result = subprocess.run(["bash", "-c", invalid], text=True, capture_output=True, check=True)
    assert "rc=97" in invalid_result.stdout
    assert "state:prior_inventory_last_status=INVALID_RESPONSE" in invalid_result.stderr
    assert "state:prior_inventory_instance_status=PASS" not in invalid_result.stderr


def test_transient_retry_keeps_causal_rc_when_delay_budget_is_short() -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    with tempfile.TemporaryDirectory() as temp_dir:
        fake = Path(temp_dir) / "fake-oci"
        fake.write_text("#!/usr/bin/env bash\necho 'connection reset' >&2\nexit 18\n")
        fake.chmod(0o755)
        script = r'''
bounded_wait_seconds() {
  if [[ "$1" == 6 ]]; then printf '2\n'; else printf '%s\n' "$1"; fi
}
primary_deadline=999999
''' + helper + r'''
if oci_json_request "$FAKE_OCI"; then echo rc=0; else printf 'rc=%s stderr=%s\n' "$?" "$OCI_JSON_RAW_ERROR"; fi
'''
        result = subprocess.run(
            ["bash", "-c", script],
            env=os.environ | {"RUNNER_TEMP": temp_dir, "FAKE_OCI": str(fake), "OCI_JSON_MAX_ATTEMPTS": "3"},
            text=True, capture_output=True, check=True,
        )
        assert "rc=18 stderr=connection reset" in result.stdout


def test_transient_retry_keeps_causal_rc_at_exact_delay_boundary() -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    with tempfile.TemporaryDirectory() as temp_dir:
        fake = Path(temp_dir) / "fake-oci"
        fake.write_text("#!/usr/bin/env bash\necho 'connection reset' >&2\nexit 18\n")
        fake.chmod(0o755)
        script = r'''
bounded_wait_seconds() {
  if [[ "$1" == 6 ]]; then printf '5\n'; else printf '%s\n' "$1"; fi
}
primary_deadline=999999
''' + helper + r'''
if oci_json_request "$FAKE_OCI"; then echo rc=0; else printf 'rc=%s stderr=%s\n' "$?" "$OCI_JSON_RAW_ERROR"; fi
'''
        result = subprocess.run(
            ["bash", "-c", script],
            env=os.environ | {"RUNNER_TEMP": temp_dir, "FAKE_OCI": str(fake), "OCI_JSON_MAX_ATTEMPTS": "3"},
            text=True, capture_output=True, check=True,
        )
        assert "rc=18 stderr=connection reset" in result.stdout


def test_invalid_json_retry_returns_causal_class_at_exact_delay_boundary() -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    with tempfile.TemporaryDirectory() as temp_dir:
        fake = Path(temp_dir) / "fake-oci"
        fake.write_text("#!/usr/bin/env bash\nprintf 'not-json'\n")
        fake.chmod(0o755)
        script = r'''
bounded_wait_seconds() {
  if [[ "$1" == 6 ]]; then printf '5\n'; else printf '%s\n' "$1"; fi
}
primary_deadline=999999
''' + helper + r'''
if oci_json_request "$FAKE_OCI"; then echo rc=0; else printf 'rc=%s error=%s\n' "$?" "$OCI_JSON_ERROR"; fi
'''
        result = subprocess.run(
            ["bash", "-c", script],
            env=os.environ | {"RUNNER_TEMP": temp_dir, "FAKE_OCI": str(fake), "OCI_JSON_MAX_ATTEMPTS": "3"},
            text=True, capture_output=True, check=True,
        )
        assert "rc=86 error=INVALID_JSON_SUCCESS_RESPONSE" in result.stdout


def test_retry_precheck_expiry_returns_previous_causal_rc() -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    with tempfile.TemporaryDirectory() as temp_dir:
        fake = Path(temp_dir) / "fake-oci"
        fake.write_text("#!/usr/bin/env bash\necho 'connection reset' >&2\nexit 18\n")
        fake.chmod(0o755)
        script = r'''
bounded_wait_seconds() {
  count=0
  [[ ! -f "$BUDGET_COUNT" ]] || count="$(cat "$BUDGET_COUNT")"
  count=$((count + 1)); printf '%s' "$count" >"$BUDGET_COUNT"
  if (( count == 1 )); then printf '%s\n' "$1"
  elif (( count == 2 )); then printf '%s\n' "$1"
  else return 42
  fi
}
primary_deadline=999999
''' + helper + r'''
if oci_json_request "$FAKE_OCI"; then echo rc=0; else printf 'rc=%s stderr=%s\n' "$?" "$OCI_JSON_RAW_ERROR"; fi
'''
        result = subprocess.run(
            ["bash", "-c", script],
            env=os.environ | {
                "RUNNER_TEMP": temp_dir,
                "BUDGET_COUNT": str(Path(temp_dir) / "budget-count"),
                "FAKE_OCI": str(fake),
                "OCI_JSON_MAX_ATTEMPTS": "3",
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            },
            text=True, capture_output=True, check=True,
        )
        assert "rc=18 stderr=connection reset" in result.stdout


def test_invalid_json_retry_precheck_expiry_returns_86() -> None:
    start = WORKFLOW.index("oci_json_request() {")
    end = WORKFLOW.index("# END OCI_JSON_REQUEST_HELPER", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    with tempfile.TemporaryDirectory() as temp_dir:
        fake = Path(temp_dir) / "fake-oci"
        fake.write_text("#!/usr/bin/env bash\nprintf 'not-json'\n")
        fake.chmod(0o755)
        script = r'''
bounded_wait_seconds() {
  count=0
  [[ ! -f "$BUDGET_COUNT" ]] || count="$(cat "$BUDGET_COUNT")"
  count=$((count + 1)); printf '%s' "$count" >"$BUDGET_COUNT"
  if (( count <= 2 )); then printf '%s\n' "$1"; else return 42; fi
}
primary_deadline=999999
''' + helper + r'''
if oci_json_request "$FAKE_OCI"; then echo rc=0; else printf 'rc=%s error=%s\n' "$?" "$OCI_JSON_ERROR"; fi
'''
        result = subprocess.run(
            ["bash", "-c", script],
            env=os.environ | {
                "RUNNER_TEMP": temp_dir,
                "BUDGET_COUNT": str(Path(temp_dir) / "budget-count"),
                "FAKE_OCI": str(fake),
                "OCI_JSON_MAX_ATTEMPTS": "3",
                "OCI_JSON_RETRY_DELAY_SECONDS": "0",
            },
            text=True, capture_output=True, check=True,
        )
        assert "rc=86 error=INVALID_JSON_SUCCESS_RESPONSE" in result.stdout


def test_receipt_publishes_prior_inventory_diagnostics() -> None:
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "prior_inventory_key + '_stderr_class'" in receipt
    assert "prior_inventory_key + '_stdout_shape'" in receipt
    assert "prior_inventory_key + '_stdout_bytes'" in receipt
    assert "prior_inventory_key + '_stderr_bytes'" in receipt


def test_issue_881_retry_runs_only_after_guarded_expansion() -> None:
    mutation = WORKFLOW.index("Expand root and recover exact image under shared host lock")
    block = WORKFLOW[mutation:WORKFLOW.index("Publish bounded operational receipt")]
    assert block.index("oracle_universal_video_root_filesystem_expand.sh") < block.index(
        "oracle_universal_video_container_missing_image_recover.sh"
    )
    assert "/usr/bin/flock -x /run/lock/oracle-workload-mutation.lock /bin/bash -s" in block
    # The mutation and the always-run fence release each serialize on the
    # shared host lock.  The recovery payload itself must still be invoked
    # under exactly one flock.
    mutation_step = block[: block.index("Release capacity workload fence")]
    assert mutation_step.count("/run/lock/oracle-workload-mutation.lock") == 1
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
    cleanup_command = "/oracle-ops issue-881-reconcile-run-34013072946"
    assert cleanup_command in WORKFLOW
    assert "/oracle-ops issue-881-reconcile-run-33893910685" not in WORKFLOW
    assert 'failed_run_id="34013072946"' in WORKFLOW
    assert 'failed_run_receipt_comment_id="5557131531"' in WORKFLOW
    assert 'target_prior_stamp="${operation_run_prefix}${failed_run_id}-a1"' in WORKFLOW
    assert 'failed_run_backup_name="${backup_prefix}-${failed_run_id}-a1"' in WORKFLOW
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
    assert "load_authoritative_failed_receipt" in WORKFLOW
    assert "prove_repeated_exact_stamp_inventory_no_active" in WORKFLOW
    assert "DIRECT_GET_TERMINAL_AND_REPEATED_STAMP_INVENTORY_NO_ACTIVE" in WORKFLOW
    assert "RECONCILED_PROVEN_ABSENT" in WORKFLOW
    assert "cleanup_typed_proof_verdicts(state) if cleanup_only else {}" in WORKFLOW
    for resource in (
        "uncertain_instance", "instance", "restored_volume", "vcn", "internet_gateway",
        "route_table", "security_list", "subnet",
    ):
        assert f"typed_verdict('{resource}')" in WORKFLOW
    assert "RECONCILIATION_INCOMPLETE" in Path(
        "ops/issue_881_failed_run_receipt.py"
    ).read_text()
    assert "Each known ID must" in WORKFLOW
    cleanup_only_branch = WORKFLOW[
        WORKFLOW.index("if (( cleanup_only == 1 )); then", WORKFLOW.index("record_reconcile_failure()")) :
        WORKFLOW.index("trap cleanup EXIT")
    ]
    assert "boot-volume-backup create" not in cleanup_only_branch
    assert "boot-volume create" not in cleanup_only_branch
    assert "compute instance launch" not in cleanup_only_branch
    assert "ssh " not in cleanup_only_branch
    assert "exit 0" in cleanup_only_branch


def test_exact_stamp_inventory_requires_three_clean_passes_and_fails_on_late_visibility() -> None:
    start = WORKFLOW.index("prove_repeated_exact_stamp_inventory_no_active()")
    end = WORKFLOW.index("reconcile_prior_attempt_resources()", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    harness = helper + r'''
state_set() { :; }
sleep() { :; }
prior_named_ids() {
  count=$(cat "$COUNTER")
  count=$((count + 1))
  printf '%s' "$count" > "$COUNTER"
  if [[ "${FAIL_ON_CALL:-0}" == "$count" ]]; then return 1; fi
  if [[ "${LATE_ON_CALL:-0}" == "$count" ]]; then echo late-visible-id; fi
}
if prove_repeated_exact_stamp_inventory_no_active; then echo rc=0; else echo rc=$?; fi
'''
    with tempfile.TemporaryDirectory() as temp_dir:
        counter = Path(temp_dir) / "counter"
        counter.write_text("0")
        clean = subprocess.run(
            ["bash", "-c", harness],
            env=os.environ | {"COUNTER": str(counter)},
            text=True,
            capture_output=True,
            check=True,
        )
        assert "rc=0" in clean.stdout
        assert counter.read_text() == "21"

        counter.write_text("0")
        late = subprocess.run(
            ["bash", "-c", harness],
            env=os.environ | {"COUNTER": str(counter), "LATE_ON_CALL": "8"},
            text=True,
            capture_output=True,
            check=True,
        )
        assert "rc=96" in late.stdout

        counter.write_text("0")
        failed = subprocess.run(
            ["bash", "-c", harness],
            env=os.environ | {"COUNTER": str(counter), "FAIL_ON_CALL": "3"},
            text=True,
            capture_output=True,
            check=True,
        )
        assert "rc=96" in failed.stdout


def test_redacted_cleanup_cannot_short_circuit_after_one_empty_inventory() -> None:
    reconcile = WORKFLOW[
        WORKFLOW.index("reconcile_prior_attempt_resources()") :
        WORKFLOW.index("cleanup_temp_resources()")
    ]
    empty_gate = reconcile[
        reconcile.index('if [[ -z "$prior_instances$prior_restored') :
        reconcile.index('if [[ -n "$prior_instances" ]]')
    ]
    assert "if (( cleanup_only == 0 )); then" in empty_gate
    assert empty_gate.index("if (( cleanup_only == 0 )); then") < empty_gate.index("return 0")
    assert "redacted_inventory=true" in empty_gate
    redacted_start = reconcile.index("redacted_inventory=true")
    repeated_proof = reconcile.index("prove_repeated_exact_stamp_inventory_no_active", redacted_start)
    typed_proof = reconcile.index("record_authoritative_type_proof instance", repeated_proof)
    success = reconcile.index("prior_cleanup_status RECONCILED_PROVEN_ABSENT", typed_proof)
    assert redacted_start < repeated_proof < typed_proof < success


def test_uncertain_instance_manifest_is_read_only_for_cleanup_only_mode() -> None:
    reconcile = WORKFLOW[
        WORKFLOW.index("reconcile_prior_attempt_resources()") :
        WORKFLOW.index("cleanup_temp_resources()")
    ]
    guard = "if (( cleanup_only == 1 )); then"
    manifest_read = 'MANIFEST="$authoritative_receipt_manifest" python -c'
    assert guard in reconcile
    assert manifest_read in reconcile
    assert reconcile.index(guard) < reconcile.index(manifest_read)
    assert '[[ -n "${authoritative_receipt_manifest:-}" ]] || return 95' in reconcile


def test_bound_resource_ids_fails_closed_when_inventory_or_receipt_lookup_fails() -> None:
    start = WORKFLOW.index("bound_resource_ids()")
    end = WORKFLOW.index("record_authoritative_type_proof()", start)
    helper = textwrap.dedent(WORKFLOW[start:end])
    harness = helper + r'''
merge_resource_ids() { printf '%s\n' "$1" "$2"; }
prior_named_ids() { [[ "${FAIL_INVENTORY:-0}" == 0 ]] || return 1; echo inventory-id; }
authoritative_receipt_ids() { [[ "${FAIL_RECEIPT:-0}" == 0 ]] || return 1; echo receipt-id; }
if output="$(bound_resource_ids instance boot-acceptance)"; then echo "rc=0:$output"; else echo "rc=$?"; fi
'''
    good = subprocess.run(["bash", "-c", harness], text=True, capture_output=True, check=True)
    assert "rc=0:inventory-id" in good.stdout and "receipt-id" in good.stdout
    bad_inventory = subprocess.run(
        ["bash", "-c", harness], env=os.environ | {"FAIL_INVENTORY": "1"}, text=True, capture_output=True, check=True
    )
    assert "rc=96" in bad_inventory.stdout
    bad_receipt = subprocess.run(
        ["bash", "-c", harness], env=os.environ | {"FAIL_RECEIPT": "1"}, text=True, capture_output=True, check=True
    )
    assert "rc=95" in bad_receipt.stdout


def test_cleanup_masks_all_exact_ids_before_public_cleanup_logs() -> None:
    reconcile = WORKFLOW[WORKFLOW.index("reconcile_prior_attempt_resources()") : WORKFLOW.index("cleanup_temp_resources()")]
    assert 'mask_resource_ids' in reconcile
    assert reconcile.index('mask_resource_ids') < reconcile.index('reconcile_bound_resource instance boot-acceptance')
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "prior_uncertain_instance_proof" in receipt


def test_empty_prior_inventory_masking_is_successful_and_reconciliation_is_diagnostic() -> None:
    mask_start = WORKFLOW.index("mask_resource_ids()")
    mask_end = WORKFLOW.index("merge_resource_ids()", mask_start)
    mask_helper = textwrap.dedent(WORKFLOW[mask_start:mask_end])
    empty = subprocess.run(
        ["bash", "-e", "-c", mask_helper + "printf '%s\\n' '' '' '' '' '' '' '' | mask_resource_ids; echo PASS"],
        text=True,
        capture_output=True,
        check=True,
    )
    assert empty.stdout.strip() == "PASS"

    invocation = WORKFLOW[
        WORKFLOW.index("record_reconcile_failure()") :
        WORKFLOW.index("if (( cleanup_only == 1 )); then", WORKFLOW.index("record_reconcile_failure()"))
    ]
    assert 'state_set prior_cleanup_failure_rc "$reconcile_rc"' in invocation
    assert 'state_set failure_phase "${phase:-reconcile_prior_attempts}"' in invocation
    assert 'exit "$reconcile_rc"' in invocation
    assert "trap 'record_reconcile_failure' ERR" in invocation
    assert "reconcile_prior_attempt_resources\n" in invocation
    assert "if reconcile_prior_attempt_resources" not in invocation
    assert invocation.index("set -E") < invocation.index("trap 'record_reconcile_failure' ERR")
    assert invocation.index("trap 'record_reconcile_failure' ERR") < invocation.index("reconcile_prior_attempt_resources\n")
    reconcile_call = invocation.index("reconcile_prior_attempt_resources\n")
    assert reconcile_call < invocation.index("trap - ERR", reconcile_call)

    diagnostic_harness = r'''
state_file="$(mktemp)"
state_set() { printf '%s=%s\n' "$1" "$2" >>"$state_file"; }
phase=reconcile_prior_attempts
record_reconcile_failure() {
  local reconcile_rc="$?"
  trap - ERR
  state_set prior_cleanup_status FAILED
  state_set prior_cleanup_failure_rc "$reconcile_rc"
  state_set failure_rc "$reconcile_rc"
  state_set failure_phase "${phase:-reconcile_prior_attempts}"
  cat "$state_file"
  exit "$reconcile_rc"
}
reconcile_prior_attempt_resources() { echo BEFORE; false; echo UNSAFE_CONTINUATION; }
set -E
trap 'record_reconcile_failure' ERR
reconcile_prior_attempt_resources
echo UNSAFE_SUCCESS
'''
    failed = subprocess.run(["bash", "-e", "-c", diagnostic_harness], text=True, capture_output=True)
    assert failed.returncode == 1
    assert "UNSAFE_CONTINUATION" not in failed.stdout
    assert "UNSAFE_SUCCESS" not in failed.stdout
    assert "prior_cleanup_failure_rc=1" in failed.stdout
    assert "failure_phase=reconcile_prior_attempts" in failed.stdout
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "value('prior_cleanup_failure_rc', 'none')" in receipt


def test_prior_direct_get_proof_is_bound_to_exact_resource_metadata() -> None:
    wait_absent = WORKFLOW[WORKFLOW.index("wait_absent()") : WORKFLOW.index("wait_all_absent()")]
    assert 'EXPECTED_NAME="$expected_name"' in wait_absent
    assert 'data.get("display-name")==expected_name' in wait_absent
    assert 'data.get("compartment-id")==os.environ["TENANCY_ID"]' in wait_absent
    assert 'kind not in {"instance","boot-volume"}' in wait_absent
    assert 'data.get("availability-domain")==os.environ["AD"]' in wait_absent

    reconcile = WORKFLOW[WORKFLOW.index("reconcile_prior_attempt_resources()") : WORKFLOW.index("cleanup_temp_resources()")]
    for resource, suffix, timeout in (
        ("instance", "boot-acceptance", 600),
        ("boot-volume", "restore", 120),
        ("subnet", "subnet", 120),
        ("security-list", "ssh-only", 120),
        ("route-table", "route", 120),
        ("internet-gateway", "ig", 120),
        ("vcn", "vcn", 120),
    ):
        validation = f'reconcile_bound_resource {resource} {suffix} {timeout}'
        deletion = f'delete_resource_once {resource} "$id"'
        assert validation in reconcile
    validator = WORKFLOW[WORKFLOW.index("validate_bound_id_before_delete()") : WORKFLOW.index("wait_instance_terminated()")]
    assert 'TARGET_PRIOR_STAMP="$target_prior_stamp"' in validator
    assert 'name.startswith(os.environ["PRIOR_PREFIX"])' in validator
    assert 'name.endswith(suffix)' in validator
    assert 'assert isinstance(state,str) and state in allowed' in validator
    assert '"SKIP" if state=="TERMINATED" else "DELETE"' in validator


def test_public_receipt_redacts_ocids_and_hashes() -> None:
    receipt = WORKFLOW[WORKFLOW.index("Publish bounded operational receipt") :]
    assert "def redacted_presence" in receipt
    for key in (
        "prior_resource_ids",
        "prior_resource_summary",
        "prior_instance_authoritative_id_hashes",
        "prior_boot_volume_authoritative_id_hashes",
        "prior_vcn_authoritative_id_hashes",
    ):
        assert f"redacted_presence('{key}')" in receipt


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


def test_positive_capacity_lease_is_watchdog_armed_restored_and_receipted() -> None:
    dispatch = WORKFLOW.index("mark_phase dispatch_paid_instance_watchdog")
    armed = WORKFLOW.index("mark_phase arm_temporary_capacity_lease")
    fence = WORKFLOW.index("mark_phase acquire_capacity_workload_fence")
    lease = WORKFLOW.index("mark_phase open_temporary_capacity_lease")
    preflight = WORKFLOW.index("mark_phase paid_capacity_preflight")
    assert dispatch < armed < fence < lease < preflight
    success_cleanup = WORKFLOW.index("if ! cleanup_temp_resources; then", preflight)
    restore = WORKFLOW.index("if ! restore_source_capacity; then", success_cleanup)
    disable_trap = WORKFLOW.index("trap - EXIT", restore)
    assert success_cleanup < restore < disable_trap
    assert "temporary production capacity lease:" in WORKFLOW


def test_negative_capacity_lease_rejects_unexpected_source_or_third_config() -> None:
    assert '[[ "$source_original_ocpus" == 6 && "$source_original_memory" == 12 ]] || return 93' in WORKFLOW
    assert '[[ "$source_lease_ocpus" == 5 && "$source_lease_memory" == 11 ]] || return 93' in WORKFLOW
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    assert 'if [[ "$ocpus/$memory" != "$SOURCE_ORIGINAL_OCPUS/$SOURCE_ORIGINAL_MEMORY" && "$ocpus/$memory" != "$SOURCE_LEASE_OCPUS/$SOURCE_LEASE_MEMORY" ]]; then' in watchdog
    assert "return 103" in watchdog
    resize = WORKFLOW[WORKFLOW.index("set_source_capacity() {") : WORKFLOW.index("open_source_capacity_lease()")]
    assert '[[ "$label" == capacity_lease ]]' in resize
    assert 'current_ocpus/$current_memory" == "$source_original_ocpus/$source_original_memory' in resize
    assert '[[ "$label" == capacity_restore ]]' in resize
    assert 'current_ocpus/$current_memory" == "$source_lease_ocpus/$source_lease_memory' in resize


def test_boundary_capacity_lease_is_exactly_one_ocpu_one_gib_and_45_minutes() -> None:
    assert "source_lease_ocpus=$((source_original_ocpus - 1))" in WORKFLOW
    assert "source_lease_memory=$((source_original_memory - 1))" in WORKFLOW
    assert "lease_restore_epoch=$(( $(date -u +%s) + 2700 ))" in WORKFLOW
    arm = WORKFLOW.index("if arm_source_capacity_lease")
    fence = WORKFLOW.index("if acquire_source_workload_fence", arm)
    resize = WORKFLOW.index("if open_source_capacity_lease", fence)
    assert arm < fence < resize
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    assert "lease_restore_epoch <= now + 2700" in watchdog


def test_capacity_resize_waits_through_compute_transitions_with_supplied_deadline() -> None:
    helper = WORKFLOW[
        WORKFLOW.index("wait_oci_resource_ready() {") :
        WORKFLOW.index("wait_restored_boot_volume_hydrated()")
    ]
    assert 'absolute_deadline="${7:-$primary_deadline}"' in helper
    assert 'bounded_wait_seconds "$max_seconds" "$absolute_deadline"' in helper
    assert 'OCI_JSON_ABSOLUTE_DEADLINE="$absolute_deadline" OCI_JSON_MAX_ATTEMPTS=1' in helper
    terminal = helper[helper.index('if [[ "$state" == TERMINATING') : helper.index("return 44", helper.index('if [[ "$state" == TERMINATING'))]
    assert "STOPPING" not in terminal
    assert "STOPPED" not in terminal
    resize = WORKFLOW[WORKFLOW.index("set_source_capacity() {") : WORKFLOW.index("open_source_capacity_lease()")]
    assert resize.count('bridge-school-dds3-frankfurt "$deadline"') == 4


def test_parent_capacity_parser_checks_status_and_cardinality_before_indexing() -> None:
    resize = WORKFLOW[WORKFLOW.index("set_source_capacity() {") : WORKFLOW.index("arm_source_capacity_lease()")]
    parser = resize.index('if ! INSTANCE="$current_json"')
    mapfile = resize.index('mapfile -t current <"$current_state_file"', parser)
    cardinality = resize.index('(( ${#current[@]} == 3 )) || return 93', mapfile)
    indexing = resize.index('current_state="${current[0]}"', cardinality)
    assert parser < mapfile < cardinality < indexing


def test_watchdog_service_release_is_retryable_after_marker_removal() -> None:
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    release = watchdog[
        watchdog.index("release_source_workload_fence() {") :
        watchdog.index("roots=", watchdog.index("release_source_workload_fence() {"))
    ]
    conditional_end = release.index("          fi", release.index('if [[ -e "$marker" ]]'))
    assert release.index("rm -f -- \"$marker\"") < conditional_end
    assert release.index("systemctl unmask universal-video-container.service") > conditional_end
    assert release.index("systemctl enable universal-video-container.service") > conditional_end
    assert release.index("systemctl start universal-video-container.service") > conditional_end
    parent_release = WORKFLOW[WORKFLOW.index("release_source_workload_fence() {") : WORKFLOW.index("set_source_capacity() {")]
    parent_end = parent_release.index("          fi", parent_release.index('if [[ -e "$marker" ]]'))
    assert parent_release.index("systemctl unmask universal-video-container.service") > parent_end
    assert parent_release.index("systemctl enable universal-video-container.service") > parent_end
    assert parent_release.index("systemctl start universal-video-container.service") > parent_end


def test_regression_capacity_is_restored_only_after_paid_instance_cleanup() -> None:
    exit_cleanup = WORKFLOW[WORKFLOW.index("cleanup() {") : WORKFLOW.index("record_reconcile_failure()")]
    assert exit_cleanup.index("cleanup_temp_resources") < exit_cleanup.index("restore_source_capacity")
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    terminate = watchdog.index("oci compute instance terminate")
    early_restore = watchdog.index("restore_source_capacity", terminate)
    volume_cleanup = watchdog.index("oci bv boot-volume delete", early_restore)
    restore_comment = watchdog.index("temporary production capacity lease is RESTORED")
    assert terminate < early_restore < volume_cleanup < restore_comment
    assert "'- media canary: ' + code('false')" in WORKFLOW


def test_cleanup_failure_restores_after_paid_compute_is_proven_terminal() -> None:
    cleanup = WORKFLOW[WORKFLOW.index("cleanup_temp_resources() {") : WORKFLOW.index("cleanup() {")]
    terminated = cleanup.index("paid_instance_terminal_proven=1")
    volume_cleanup = cleanup.index('if [[ -n "$restored_id" ]]', terminated)
    restore = cleanup.index("if restore_source_capacity; then", terminated)
    fence_receipt = cleanup.index("CAPACITY_FENCE_HELD", restore)
    assert terminated < restore < fence_receipt < volume_cleanup
    assert "release_source_workload_fence" not in cleanup[terminated:volume_cleanup]
    failure = WORKFLOW[WORKFLOW.index("if ! cleanup_temp_resources; then", WORKFLOW.index("mark_phase paid_capacity_preflight")) :]
    failure = failure[:failure.index("if ! restore_source_capacity; then")]
    assert "if (( paid_instance_terminal_proven == 1 )); then" in failure
    assert failure.index("restore_source_capacity || true") < failure.index("trap - EXIT")
    assert failure.index("release_source_workload_fence || true") < failure.index("trap - EXIT")


def test_watchdog_releases_fence_even_when_capacity_restore_fails() -> None:
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    start = watchdog.index("source_restore_rc=0")
    block = watchdog[start:watchdog.index("# The primary runner normally removes", start)]
    assert block.index("restore_source_capacity || source_restore_rc=$?") < block.index("release_source_workload_fence || source_release_rc=$?")
    assert "exit 105" not in block
    volume_cleanup = watchdog.index("oci bv boot-volume delete", start)
    deferred_failure = watchdog.index("if (( source_restore_rc != 0 || source_release_rc != 0 )); then", volume_cleanup)
    assert volume_cleanup < deferred_failure < watchdog.index("exit 105", deferred_failure)


def test_watchdog_propagates_source_identity_and_parser_failures() -> None:
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    restore = watchdog[watchdog.index("restore_source_capacity() {") : watchdog.index("release_source_workload_fence() {")]
    hmac_check = restore.index("assert hmac.compare_digest")
    assert restore.index("[[ $? == 0 ]] || return 103", hmac_check) > hmac_check
    parser = restore.index('if ! EXPECTED_ID="$source_id"')
    mapfile = restore.index('mapfile -t source_state <"$source_state_file"', parser)
    cardinality = restore.index('(( ${#source_state[@]} == 3 )) || return 104', mapfile)
    indexing = restore.index('lifecycle="${source_state[0]}"', cardinality)
    assert parser < mapfile < cardinality < indexing
    softstop = restore.index("--action SOFTSTOP")
    first_only = restore.index("if (( source_softstop_issued == 0 )); then", indexing)
    budget = restore.index("source_restore_deadline - SECONDS >= 600", first_only)
    issued = restore.index("source_softstop_issued=1", budget)
    assert first_only < budget < issued < softstop
    assert restore.count("source_restore_deadline - SECONDS >= 600") == 1


def test_watchdog_preserves_fence_for_bounded_live_parent_mutation_phase() -> None:
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    start = watchdog.index("CAPACITY_FENCE_HELD")
    release = watchdog.index("release_source_workload_fence || source_release_rc=$?", start)
    block = watchdog[start:release]
    assert "fence_wait_deadline=$((fence_now + 2700))" in block
    assert 'fence_wait_deadline="$watchdog_cleanup_cutoff_epoch"' in block
    assert "fence_release_epoch <= fence_now" in block
    assert "fence_release_epoch <= fence_wait_deadline" in block
    assert 'actions/runs/${PARENT_RUN_ID}' in block
    assert 'if [[ "$parent_status" != completed ]]; then' in block
    assert '[[ "$parent_status" == completed ]] && break' in block
    assert "release_source_workload_fence" not in block


def test_watchdog_retries_paid_reconciliation_with_reserved_cleanup_time() -> None:
    watchdog = Path(".github/workflows/issue-881-paid-instance-watchdog.yml").read_text()
    assert "watchdog_hard_deadline=$((SECONDS + 17400))" in watchdog
    assert "watchdog_cleanup_cutoff=$((watchdog_hard_deadline - 600))" in watchdog
    assert "watchdog_cleanup_cutoff_epoch=$(( $(date -u +%s) + watchdog_cleanup_cutoff - SECONDS ))" in watchdog
    start = watchdog.index("reconcile_paid_instance_once() {")
    loop = watchdog.index("while (( SECONDS < watchdog_cleanup_cutoff )); do", start)
    proof = watchdog.index("instance_terminal_proven == 1 || instance_inventory_clean == 1", loop)
    assert start < loop < proof
    reconcile = watchdog[start:loop]
    assert "exit 97" not in reconcile
    assert "exit 99" not in reconcile
    assert "exit 101" not in reconcile
    restore = watchdog[watchdog.index("restore_source_capacity() {") : watchdog.index("release_source_workload_fence() {")]
    assert 'source_restore_deadline="$watchdog_cleanup_cutoff"' in restore


def test_exit_cleanup_does_not_reclaim_capacity_before_paid_terminal_proof() -> None:
    cleanup = WORKFLOW[WORKFLOW.index("cleanup() {") : WORKFLOW.index("record_reconcile_failure()")]
    gate = cleanup.index("if (( paid_launch_request_started == 0 || paid_instance_terminal_proven == 1 )); then")
    restore = cleanup.index("restore_source_capacity || cleanup_rc=1", gate)
    release = cleanup.index("release_source_workload_fence || cleanup_rc=1", restore)
    conditional_end = cleanup.index("fi", release)
    assert gate < restore < release < conditional_end
    launch = WORKFLOW.index("paid_launch_request_started=1")
    create = WORKFLOW.index("create_json_once paid_instance_create", launch)
    assert launch < create


def test_exit_cleanup_stays_armed_through_fallible_backup_retirement() -> None:
    acceptance = WORKFLOW.index("backup_accepted=1")
    retirement = WORKFLOW.index("wait_all_absent backup", acceptance)
    disable = WORKFLOW.index("trap - EXIT", retirement)
    gate = WORKFLOW.index("UV_ROOT_BACKUP_GATE_PASS", disable)
    assert acceptance < retirement < disable < gate
    cleanup = WORKFLOW[WORKFLOW.index("cleanup() {") : WORKFLOW.index("record_reconcile_failure()")]
    assert "release_source_workload_fence || cleanup_rc=1" in cleanup


def test_always_run_fence_release_rebuilds_pinned_ssh_identity() -> None:
    start = WORKFLOW.index("Release capacity workload fence and ensure sole service active")
    release = WORKFLOW[start:WORKFLOW.index("Publish bounded operational receipt", start)]
    assert "steps.ssh.outputs.key" not in release
    assert "steps.ssh.outputs.known" not in release
    assert 'ops/oracle_known_hosts_from_scan.sh "$ORACLE_HOST" "$EXPECTED_FINGERPRINT" "$release_known"' in release
    assert 'printf \'%s\\n\' "$SSH_KEY_ORACLE" >"$release_key"' in release
    assert 'ssh-keygen -y -f "$release_key"' in release
    assert 'ssh -i "$release_key"' in release
    assert 'UserKnownHostsFile="$release_known"' in release
