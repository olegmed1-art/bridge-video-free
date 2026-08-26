from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops/repair_universal_video_runtime_pin.sh"


def text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_repair_script_is_valid_bash_and_stays_bounded():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    source = text()
    assert "readonly SERVICE='universal-video.service'" in source
    assert "assistant-lab.service" in source
    assert "UV003_RUNTIME_PIN_REPAIR=PASS" in source
    for forbidden in (
        "spool/inbox/",
        "ffmpeg ",
        "faster_whisper",
        "GOOGLE_DRIVE",
        "drive_adapter",
        "submit_for",
        "enqueue(",
    ):
        assert forbidden not in source


def test_env_shape_gate_is_silent_and_has_no_shell_round_trip():
    source = text()
    gate = source.split("failure_stage='ENV_SHAPE'", 1)[1].split("failure_stage='BACKUP'", 1)[0]
    assert "UV003_ENV_SHAPE_VALIDATOR_V2" in source
    assert 'env_shape="$' not in gate
    assert "O_NOFOLLOW" in gate
    assert "raw.decode('utf-8', errors='strict')" in gate
    assert "assert '=' in line" in gate
    assert "print(" not in gate
    assert "UNIVERSAL_VIDEO_SOURCE_COMMIT=" not in gate


def test_write_failure_is_rollback_eligible_before_replacement():
    source = text()
    write = source.split("failure_stage='WRITE_ENV'", 1)[1].split("failure_stage='VERIFY_FILE'", 1)[0]
    assert write.index("changed=1") < write.index("os.replace(tmp,p)")


def test_rollback_restarts_old_env_after_new_process_failure():
    source = text()
    rollback = source.split("rollback(){", 1)[1].split("trap rollback EXIT", 1)[0]
    assert "trap - ERR" in rollback
    assert "service_started_new == 1" in rollback
    stop_at = rollback.index('systemctl stop "$SERVICE"')
    restore_at = rollback.index('cp --preserve=mode,ownership,timestamps "$backup" "$ENV_FILE"')
    start_at = rollback.index('systemctl start "$SERVICE"')
    assert stop_at < restore_at < start_at
    start_stage = source.split("failure_stage='START_SERVICE'", 1)[1].split("failure_stage='VERIFY_LIVE'", 1)[0]
    assert "service_started_new=1" in start_stage


def test_global_err_trap_emits_the_fixed_failure_surface():
    source = text()
    assert "on_error(){" in source
    assert "trap on_error ERR" in source
    on_error = source.split("on_error(){", 1)[1].split("trap on_error ERR", 1)[0]
    assert "trap - ERR" in on_error
    assert "report_failure" in on_error
    fail = source.split("fail(){", 1)[1].split("checkpoint(){", 1)[0]
    assert "trap - ERR" in fail
    assert "report_failure" in fail


def test_stage_checkpoints_are_fixed_and_non_sensitive():
    source = text()
    expected = (
        "PRECHECK_PASS",
        "READY_BEFORE_PASS",
        "SPOOL_BEFORE_PASS",
        "ENV_SHAPE_ENTER",
        "ENV_SHAPE_PASS",
        "BACKUP_ENTER",
        "BACKUP_PASS",
        "ROLLBACK_ARMED",
        "STOP_SERVICE_ENTER",
        "STOP_SERVICE_PASS",
        "WRITE_ENV_ENTER",
        "WRITE_ENV_PASS",
        "VERIFY_FILE_PASS",
        "SPOOL_AFTER_WRITE_PASS",
        "START_SERVICE_ENTER",
        "START_SERVICE_PASS",
        "VERIFY_LIVE_PASS",
        "SPOOL_AFTER_START_PASS",
        "READY_AFTER_PASS",
        "ASSISTANT_AFTER_PASS",
        "FINALIZE_ENTER",
    )
    checkpoint_fn = source.split("checkpoint(){", 1)[1].split("on_error(){", 1)[0]
    for value in expected:
        assert value in checkpoint_fn
        assert f"checkpoint {value}" in source
    assert "UV003_RUNTIME_PIN_CHECKPOINT=%s" in checkpoint_fn
    assert "$EXPECTED_RUNTIME_COMMIT" not in checkpoint_fn
    assert "$ENV_FILE" not in checkpoint_fn


def test_all_failure_stages_remain_fixed_and_allowlisted():
    source = text()
    stages = (
        "PRECHECK",
        "READY_BEFORE",
        "SPOOL_BEFORE",
        "ENV_SHAPE",
        "BACKUP",
        "STOP_SERVICE",
        "WRITE_ENV",
        "VERIFY_FILE",
        "SPOOL_AFTER_WRITE",
        "START_SERVICE",
        "VERIFY_LIVE",
        "SPOOL_AFTER_START",
        "READY_AFTER",
        "ASSISTANT_AFTER",
        "FINALIZE",
    )
    allowlist = source.split('case "$failure_stage" in', 1)[1].split("esac", 1)[0]
    for stage in stages:
        assert stage in allowlist
        assert f"failure_stage='{stage}'" in source or stage == "PRECHECK"
