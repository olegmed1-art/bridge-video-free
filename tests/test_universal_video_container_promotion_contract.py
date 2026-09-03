from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "ops/oracle_universal_video_container_promote.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-container-promote.yml").read_text(encoding="utf-8")
EVIDENCE_WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-container-evidence.yml").read_text(encoding="utf-8")
OPERATOR_INSTALL = (ROOT / "ops/install_universal_video_operator.sh").read_text(encoding="utf-8")


def test_promotion_is_evidence_bound_serialized_and_reversible() -> None:
    assert "validate_universal_video_promotion_evidence.py select-artifact" in WORKFLOW
    assert "validate_universal_video_promotion_evidence.py verify-archive" in WORKFLOW
    assert "actions/runs/$evidence_run_id/artifacts?per_page=100" in WORKFLOW
    assert "actions/artifacts/$artifact_id/zip" in WORKFLOW
    assert '--expected-artifact-digest "$artifact_digest"' in WORKFLOW
    assert '--expected-image-digest "$image_digest"' in WORKFLOW
    assert "group: oracle-instance-workload-mutation" in WORKFLOW
    assert "ORACLE_INSTANCE_RUNNING_PASS" in WORKFLOW
    assert "compute instance action --instance-id \"$INSTANCE_ID\" --action START" in WORKFLOW
    assert "rollback" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_ROLLED_BACK" in SCRIPT
    assert "stage=%s rc=%s" in SCRIPT
    for stage in ("installer-activation", "service-verification", "resident-status", "protected-postflight"):
        assert f"CURRENT_STAGE='{stage}'" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_JOB_RUNNING" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_WORKLOAD_LOCK_INVALID" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_WORKLOAD_BUSY" in SCRIPT
    assert "UNIVERSAL_VIDEO_CONTAINER_BUILD=0" in SCRIPT
    assert "contents/ops/oracle_universal_video_container_promote.sh?ref=$EXPECTED_COMMIT" in WORKFLOW
    assert "git hash-object -- /opt/bridge-school/universal-video-src/ops/oracle_universal_video_container_promote.sh" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_ENTRYPOINT_PASS" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_SOURCE_MISMATCH" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_SOURCE_PREPARE_PASS" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_SOURCE_PREPARE_FAILED" in WORKFLOW
    assert "UNIVERSAL_VIDEO_ACTIVATE=1" in WORKFLOW
    assert "systemctl is-active --quiet universal-video-container.service" in WORKFLOW
    assert "expected_prepare_blob" in WORKFLOW
    assert "expected_preflight_blob" in WORKFLOW
    assert "expected_dsn_validator_blob" in WORKFLOW
    assert 'git hash-object "$RUNNER_TEMP/prepare.sh"' in WORKFLOW
    assert 'git hash-object "$RUNNER_TEMP/prepromotion-preflight.sh"' in WORKFLOW
    assert 'git hash-object "$RUNNER_TEMP/validate-video-queue-dsn.py"' in WORKFLOW
    assert '--jq .content | base64 --decode > "$RUNNER_TEMP/prepare.sh"' in WORKFLOW
    assert "tr -d" not in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_ENTRYPOINT_MISSING" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_BLOB_MISMATCH" in WORKFLOW
    assert " /bin/bash /opt/bridge-school/universal-video-src/ops/oracle_universal_video_container_promote.sh" in WORKFLOW


def test_promotion_holds_exclusive_workload_fence_through_resident_switch() -> None:
    lock_path = SCRIPT.index('readonly WORKLOAD_LOCK="$BASE_DIR/spool/.workload.lock"')
    lock_metadata = SCRIPT.index("root:universal-video:640:1", lock_path)
    lock_acquire = SCRIPT.index("flock --exclusive --nonblock 9", lock_metadata)
    final_idle = SCRIPT.index("has_running_job && fail UV_CONTAINER_PROMOTION_JOB_RUNNING", lock_acquire)
    legacy_stop = SCRIPT.index('systemctl disable --now "$OLD_SERVICE"', final_idle)
    new_start = SCRIPT.index('oracle_universal_video_container_install.sh', legacy_stop)
    assert lock_path < lock_metadata < lock_acquire < final_idle < legacy_stop < new_start
    assert 'exec 9<"$WORKLOAD_LOCK"' in SCRIPT


def test_promotion_runs_exact_queue_and_speaker_gates_before_source_preparation() -> None:
    preflight = WORKFLOW.index('"$RUNNER_TEMP/prepromotion-preflight.sh"')
    queue_gate = WORKFLOW.index("VIDEO_QUEUE_DSN_PREFLIGHT_PASS", preflight)
    speaker_gate = WORKFLOW.index("UNIVERSAL_VIDEO_PREPROMOTION_PREFLIGHT_PASS", queue_gate)
    source_prepare = WORKFLOW.index(
        "UNIVERSAL_VIDEO_GIT_REF='$EXPECTED_COMMIT'", speaker_gate
    )

    assert queue_gate < speaker_gate < source_prepare
    assert "/opt/bridge-school/universal-video/.venv/bin/python -" in WORKFLOW
    assert "validate-video-queue-dsn.py" in WORKFLOW
    assert "test ! -L /opt/bridge-school/universal-video/secrets/video-queue-dsn" in WORKFLOW
    assert 'credential_meta="$(sudo -n stat -c' in WORKFLOW
    assert 'credential_meta="$(stat -c' not in WORKFLOW


def test_promotion_selects_exact_image_and_excludes_legacy_worker() -> None:
    assert '"$(docker inspect --format \'{{.Image}}\' universal-video-container)" == "$EXPECTED_DIGEST"' in SCRIPT
    assert 'systemctl is-active --quiet "$OLD_SERVICE" && fail UV_CONTAINER_PROMOTION_LEGACY_ACTIVE' in SCRIPT
    assert "x.get('active_jobs') == []" in SCRIPT
    assert "observed_at_unix" in SCRIPT
    assert "fallback_used=false active_jobs=0" in SCRIPT


def test_promotion_requires_a_fresh_status_from_the_new_resident() -> None:
    assert "fresh_status=0" in SCRIPT
    assert "fresh_status=1" in SCRIPT
    assert "(( fresh_status != 1 ))" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_STATUS_MISSING" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_STATUS_STALE" in SCRIPT
    assert "float(x.get('observed_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])" in SCRIPT
    assert "x.get('resident_id') == 'container'" in SCRIPT
    assert "x['process_id'] == int(os.environ['EXPECTED_PROCESS_ID'])" in SCRIPT
    assert "x['process_start_ticks'] == int(os.environ['EXPECTED_PROCESS_START_TICKS'])" in SCRIPT
    assert "re.fullmatch(r'[0-9a-f]{32}', x['process_nonce'])" in SCRIPT
    assert "docker inspect --format '{{.State.Pid}}' universal-video-container" in SCRIPT
    assert "pid_descends_from \"$worker_pid\" \"$container_root_pid\"" in SCRIPT
    assert "NSpid:" in SCRIPT
    assert 'PROCESS_STAT="/proc/$process_id/stat"' in SCRIPT
    assert '[[ "$(process_start_ticks "$worker_pid"' in SCRIPT
    assert "if resident_status_ready; then" in SCRIPT
    assert "CURRENT_STAGE='resident-status-final'" in SCRIPT
    assert "resident_status_ready || fail UV_CONTAINER_PROMOTION_STATUS_STALE" in SCRIPT
    assert SCRIPT.index("CURRENT_STAGE='protected-postflight'") < SCRIPT.index(
        "CURRENT_STAGE='resident-status-final'"
    ) < SCRIPT.index("CURRENT_STAGE='complete'")


def test_promotion_exposes_only_structured_container_runtime_failure_code() -> None:
    assert 'journalctl -u "$NEW_SERVICE" --since "$since" --no-pager -o cat' in SCRIPT
    assert 'set(value)=={"error_code","status"}' in SCRIPT
    assert 're.fullmatch(r"UV_CONTAINER_[A-Z0-9_]+"' in SCRIPT
    assert 'json.dumps(value,separators=(",",":"),sort_keys=True)' in SCRIPT
    assert '\\{"error_code":"UV_CONTAINER_[A-Z0-9_]+","status":"FAILED"\\}' in WORKFLOW
    assert "Result=[A-Za-z-]+" in WORKFLOW
    assert "ExecMain(Code|Status)=[0-9]+" in WORKFLOW
    assert "ExecStart(Pre)?Status[0-9]+=[0-9]+" in WORKFLOW
    assert "NRestarts=[0-9]+" in WORKFLOW


def test_promotion_runtime_diagnostic_is_fresh_bounded_and_secret_safe() -> None:
    assert 'local since="@${started_unix:-0}"' in SCRIPT
    assert 'journalctl -u "$NEW_SERVICE" --since "$since"' in SCRIPT
    assert "journalctl -u \"$NEW_SERVICE\" -n 80" not in SCRIPT
    for code in (
        "UV_CONTAINER_STARTUP_PERMISSION_DENIED",
        "UV_CONTAINER_STARTUP_DISK_FULL",
        "UV_CONTAINER_STARTUP_MEMORY_UNAVAILABLE",
        "UV_CONTAINER_STARTUP_NAME_CONFLICT",
        "UV_CONTAINER_DOCKER_RUN_FAILED",
        "UV_CONTAINER_WORKER_STARTUP_EXCEPTION",
    ):
        assert code in SCRIPT
    assert "print(line)" not in SCRIPT


def test_promotion_waits_boundedly_for_named_container_before_inspect() -> None:
    assert "process_deadline=$((SECONDS + 30))" in SCRIPT
    assert "process_ready=0" in SCRIPT
    assert "while (( SECONDS < process_deadline ))" in SCRIPT
    assert "systemctl is-active --quiet \"$NEW_SERVICE\" || break" in SCRIPT
    assert "(( process_ready == 1 )) || fail UV_CONTAINER_PROMOTION_PROCESS_INACTIVE" in SCRIPT
    assert SCRIPT.index("process_deadline=") < SCRIPT.index("CURRENT_STAGE='resident-status'")


def test_promotion_waits_boundedly_for_systemd_service_before_process_probe() -> None:
    assert "service_deadline=$((SECONDS + 30))" in SCRIPT
    assert "service_ready=0" in SCRIPT
    assert "while (( SECONDS < service_deadline ))" in SCRIPT
    assert "(( service_ready == 1 )) || fail UV_CONTAINER_PROMOTION_SERVICE_INACTIVE" in SCRIPT
    assert SCRIPT.index("service_deadline=") < SCRIPT.index("process_deadline=")


def test_post_switch_failures_invoke_rollback_directly() -> None:
    fail_body = SCRIPT[SCRIPT.index("fail(){"):SCRIPT.index("has_running_job(){")]
    assert "if (( switch_started == 1 )); then" in fail_body
    assert "rollback 1" in fail_body
    assert 'local rc="${1:-$?}"' in SCRIPT


def test_promotion_disables_legacy_and_rollback_restores_original_state() -> None:
    assert "CURRENT_STAGE='queue-credential-preflight'" in SCRIPT
    assert 'validate_video_queue_dsn.py" "$queue_dsn_file"' in SCRIPT
    assert SCRIPT.index("CURRENT_STAGE='queue-credential-preflight'") < SCRIPT.index(
        "CURRENT_STAGE='legacy-quiesce'"
    )
    assert SCRIPT.index("CURRENT_STAGE='speaker-model-preflight'") < SCRIPT.index(
        "CURRENT_STAGE='legacy-quiesce'"
    )
    speaker_preflight = SCRIPT[
        SCRIPT.index("CURRENT_STAGE='speaker-model-preflight'") :
        SCRIPT.index("CURRENT_STAGE='legacy-quiesce'")
    ]
    assert '"$EXPECTED_DIGEST" true' in speaker_preflight
    assert "UV_CONTAINER_PROMOTION_SPEAKER_MODEL_INVALID" in speaker_preflight
    assert 'old_enabled_before="$(systemctl is-enabled "$OLD_SERVICE"' in SCRIPT
    assert 'old_active_before="$(systemctl is-active "$OLD_SERVICE"' in SCRIPT
    assert 'systemctl disable --now "$OLD_SERVICE" || fail UV_CONTAINER_PROMOTION_LEGACY_QUIESCE_FAILED' in SCRIPT
    assert 'CURRENT_STAGE=\'legacy-quiesce\'' in SCRIPT
    assert 'systemctl enable "$OLD_SERVICE"' in SCRIPT
    assert 'if [[ "$old_active_before" == active ]]; then' in SCRIPT
    assert "UV_CONTAINER_PROMOTION_LEGACY_ENABLED" in SCRIPT


def test_promotion_atomically_syncs_revision_bound_operator() -> None:
    assert "CURRENT_STAGE='operator-snapshot'" in SCRIPT
    assert 'EXPECTED_RUNTIME_COMMIT="$EXPECTED_COMMIT"' in SCRIPT
    assert 'bash "$SOURCE_DIR/ops/install_universal_video_operator.sh"' in SCRIPT
    assert 'git hash-object "$OPERATOR_TARGET"' in SCRIPT
    assert 'rev-parse "$EXPECTED_COMMIT:ops/universal_video_operator.sh"' in SCRIPT
    assert "CURRENT_STAGE='operator-install'" in SCRIPT
    assert "CURRENT_STAGE='operator-blob'" in SCRIPT
    assert "CURRENT_STAGE='operator-smoke'" in SCRIPT
    assert 'sudo -u ocarun sudo -n "$OPERATOR_TARGET" status ..' in SCRIPT
    assert 'if operator_smoke="$(sudo -u ocarun sudo -n "$OPERATOR_TARGET" status .. 2>&1)"; then' in SCRIPT
    assert "operator_smoke_rc=0" in SCRIPT
    assert "operator_smoke_rc=$?" in SCRIPT
    smoke = SCRIPT[SCRIPT.index("CURRENT_STAGE='operator-smoke'"):SCRIPT.index("CURRENT_STAGE='protected-postflight'")]
    assert "set +e" not in smoke


def test_operator_installer_failures_have_bounded_nonsecret_codes() -> None:
    assert 'if ! operator_install_output="$(' in SCRIPT
    assert '2>&1' in SCRIPT
    assert 'fail "$code"' in SCRIPT
    assert 'UV_CONTAINER_PROMOTION_OPERATOR_INSTALL_FAILED' in SCRIPT
    assert 'UV_CONTAINER_PROMOTION_OPERATOR_SOURCE_DIRTY' in SCRIPT
    assert 'UV_CONTAINER_PROMOTION_OPERATOR_SOURCE_MISMATCH' in SCRIPT
    assert 'UV_CONTAINER_PROMOTION_OPERATOR_OBSOLETE_UNSAFE' in SCRIPT
    assert 'UV_CONTAINER_PROMOTION_OPERATOR_INSTALL_ATTESTATION_MISSING' in SCRIPT
    assert 'echo "$operator_install_output"' not in SCRIPT
    for reason in (
        "staging directory install failed",
        "temporary sudoers file unavailable",
        "temporary sudoers mode failed",
        "operator sudoers validation failed",
        "operator target install failed",
        "operator sudoers install failed",
        "system sudoers validation failed",
        "post-retirement sudoers validation failed",
    ):
        assert reason in OPERATOR_INSTALL


def test_operator_sync_is_restored_by_promotion_rollback() -> None:
    assert 'operator_snapshot_ready=0' in SCRIPT
    assert 'install -o root -g root -m 0600 "$OPERATOR_TARGET"' in SCRIPT
    assert 'install -o root -g root -m 0600 "$OPERATOR_SUDOERS"' in SCRIPT
    assert 'install -o root -g root -m 0755 "$operator_backup_root/operator" "$OPERATOR_TARGET"' in SCRIPT
    assert 'install -o root -g root -m 0440 "$operator_backup_root/sudoers" "$OPERATOR_SUDOERS"' in SCRIPT
    assert 'rm -f -- "$OPERATOR_TARGET"' in SCRIPT
    assert 'rm -f -- "$OPERATOR_SUDOERS"' in SCRIPT


def test_retired_container_evidence_cannot_run_on_operator_changes() -> None:
    assert EVIDENCE_WORKFLOW.startswith(
        "name: Retired Oracle Universal Video Container Evidence Contract\n"
    )
    assert "name: Oracle Universal Video Container Evidence\n" not in EVIDENCE_WORKFLOW
    assert "  retired-evidence-contract:" in EVIDENCE_WORKFLOW
    assert "name: Retired legacy evidence entrypoint contract" in EVIDENCE_WORKFLOW
    assert "pull_request:" in EVIDENCE_WORKFLOW
    assert "workflow_dispatch:" not in EVIDENCE_WORKFLOW
    assert "push:" not in EVIDENCE_WORKFLOW
    assert "- 'ops/universal_video_operator.sh'" not in EVIDENCE_WORKFLOW
    assert "- 'ops/install_universal_video_operator.sh'" not in EVIDENCE_WORKFLOW
    assert "UNIVERSAL_VIDEO_LEGACY_CONTAINER_EVIDENCE_RETIRED=true" in EVIDENCE_WORKFLOW
