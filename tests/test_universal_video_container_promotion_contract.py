from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "ops/oracle_universal_video_container_promote.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/oracle-universal-video-container-promote.yml").read_text(encoding="utf-8")


def test_promotion_is_evidence_bound_serialized_and_reversible() -> None:
    assert "assert x.get('conclusion') == 'success'" in WORKFLOW
    assert "assert x.get('head_sha') == os.environ['EXPECTED_COMMIT']" in WORKFLOW
    assert "group: oracle-instance-workload-mutation" in WORKFLOW
    assert "ORACLE_INSTANCE_RUNNING_PASS" in WORKFLOW
    assert "compute instance action --instance-id \"$INSTANCE_ID\" --action START" in WORKFLOW
    assert "rollback" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_ROLLED_BACK" in SCRIPT
    assert "stage=%s rc=%s" in SCRIPT
    for stage in ("installer-activation", "service-verification", "resident-status", "protected-postflight"):
        assert f"CURRENT_STAGE='{stage}'" in SCRIPT
    assert "UV_CONTAINER_PROMOTION_JOB_RUNNING" in SCRIPT
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
    assert 'git hash-object "$RUNNER_TEMP/prepare.sh"' in WORKFLOW
    assert '--jq .content | base64 --decode > "$RUNNER_TEMP/prepare.sh"' in WORKFLOW
    assert "tr -d" not in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_ENTRYPOINT_MISSING" in WORKFLOW
    assert "UV_CONTAINER_PROMOTION_BLOB_MISMATCH" in WORKFLOW
    assert " /bin/bash /opt/bridge-school/universal-video-src/ops/oracle_universal_video_container_promote.sh" in WORKFLOW


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
    assert SCRIPT.count("float(x.get('observed_at_unix') or 0) >= int(os.environ['STARTED_UNIX'])") == 2


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
    assert 'old_enabled_before="$(systemctl is-enabled "$OLD_SERVICE"' in SCRIPT
    assert 'old_active_before="$(systemctl is-active "$OLD_SERVICE"' in SCRIPT
    assert 'systemctl disable --now "$OLD_SERVICE" || fail UV_CONTAINER_PROMOTION_LEGACY_QUIESCE_FAILED' in SCRIPT
    assert 'CURRENT_STAGE=\'legacy-quiesce\'' in SCRIPT
    assert 'systemctl enable "$OLD_SERVICE"' in SCRIPT
    assert 'if [[ "$old_active_before" == active ]]; then' in SCRIPT
    assert "UV_CONTAINER_PROMOTION_LEGACY_ENABLED" in SCRIPT
