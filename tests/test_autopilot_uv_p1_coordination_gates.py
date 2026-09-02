from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_registration_reconciles_all_live_heads_before_database_connection() -> None:
    runtime = _read("ops/autopilot/register_uv_p1_runtime.py")
    workflow = _read(".github/workflows/autopilot-uv-p1-register-v3.yml")

    assert runtime.index("_verify_live_heads()") < runtime.index("connection = _connect_runtime()")
    for name in ("LIVE_RUNTIME_HEAD_SHA", "LIVE_CANARY_HEAD_SHA", "LIVE_IDLE_HEAD_SHA"):
        assert name in runtime
        assert name in workflow
    assert "runtime:997 canary:1062 idle:1047" in workflow
    assert "1000" not in runtime
    assert workflow.index("Resolve live PR heads immediately before registration") < workflow.index(
        "Register and observe through runtime-only ingress"
    )
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "allow_temporary_registration == 'YES'" in workflow


def test_monitor_rejects_missing_or_malformed_durable_task_ids() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    assert "runtime:997 canary:1062 idle:1047" in workflow
    assert "PR #1000" not in workflow
    assert "UV_AUTOPILOT_DURABLE_TASKS_MISSING={missing}" in workflow
    assert "raise SystemExit(76)" in workflow
    assert "[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}" in workflow
    validation = workflow.split("UV_AUTOPILOT_LIVE_BRANCH_VERIFIED=PASS", 1)[1]
    assert 'if [[ "$task_id" != NOT_FOUND ]]' not in validation


def test_monitor_selects_exact_shadow_worker_and_allows_only_observer_peer() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    assert "worker=school-autopilot-shadow.service" in workflow
    assert "observer=school-autopilot-online-observer.service" in workflow
    assert "canary=school-autopilot-production-canary.service" in workflow
    assert 'MainPID --value "$worker"' in workflow
    assert "UV_AUTOPILOT_PRODUCTION_CANARY_DISABLED=YES" in workflow
    assert "UV_AUTOPILOT_ACTIVE_UNIT_ALLOWLIST=PASS" in workflow
    assert "UV_AUTOPILOT_LIVE_RUNTIME_MODE=SHADOW" in workflow
    assert "modes != ['SHADOW']" in workflow
    assert '[[ "$active_unit" == "$worker" || "$active_unit" == "$observer" ]]' in workflow
    assert "systemctl list-units --all" not in workflow
    assert "UV_AUTOPILOT_UNIT_COUNT" not in workflow


def test_monitor_uses_runtime_psycopg_and_only_the_granted_status_view() -> None:
    workflow = _read(".github/workflows/autopilot-uv-p1-oracle-monitor.yml")

    assert "runtime_python=/opt/bridge-school/school-autopilot/.venv/bin/python" in workflow
    assert "import psycopg" in workflow
    assert "default_transaction_read_only=on" in workflow
    assert "LEFT JOIN autopilot.task_status" in workflow
    assert "FROM autopilot.task " not in workflow
    assert "autopilot.task_event" not in workflow
    assert "autopilot.evidence" not in workflow
    assert "command -v psql" not in workflow
    assert 'EXPECTED_PROJECT_ID: misty-poetry-18012774' in workflow
    assert "safe_reason = re.sub" in workflow


def test_runtime_gate_paginates_and_trusts_only_designated_reviewers() -> None:
    gate = _read("ops/verify_uv_runtime_pr_gate.sh")

    assert "pageInfo{hasNextPage endCursor}" in gate
    assert "gh api --paginate --slurp" in gate
    assert '== "chatgpt-codex-connector[bot]"' in gate
    assert '"vercel[bot]"' not in gate
    assert "total == reported_total" in gate
    assert "check_runs=%s/%s" in gate
    assert 'state == "CHANGES_REQUESTED"' in gate
    assert "trusted > 0 && changes_requested == 0 && unresolved == 0" in gate


def test_runtime_image_build_has_a_last_second_exact_head_gate() -> None:
    workflow = _read(".github/workflows/autopilot-uv-runtime-exact-image-gate.yml")

    recheck = workflow.index("Revalidate live gates immediately before image build")
    build = workflow.index("Prepare isolated source and build non-activated image")
    assert recheck < build
    assert 'grep -Fx "head=$EXPECTED_HEAD_SHA"' in workflow
    assert "grep -Fx 'changes_requested=0'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "allow_oracle_image_build == 'YES'" in workflow


def test_image_preflight_cannot_control_services_or_replace_resident_source() -> None:
    workflow = _read(".github/workflows/autopilot-uv-runtime-exact-image-gate.yml")
    preflight = _read("ops/oracle_universal_video_container_build_preflight.sh")

    assert "oracle_universal_video_run_command.sh" not in workflow
    assert "/opt/bridge-school/universal-video-src" not in workflow
    assert "UNIVERSAL_VIDEO_BUILD_SOURCE_DIR" in preflight
    for forbidden in ("systemctl", "service ", "universal-video.service", "daemon-reload"):
        assert forbidden not in preflight
