from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OPERATOR_PATH = ROOT / "ops/universal_video_operator.sh"
OPERATOR = OPERATOR_PATH.read_text(encoding="utf-8")


def _function(name: str, next_name: str) -> str:
    start = OPERATOR.index(f"{name}(){{")
    end = OPERATOR.index(f"\n{next_name}(){{", start)
    return OPERATOR[start:end]


def test_operator_shell_syntax_is_valid() -> None:
    subprocess.run(["bash", "-n", str(OPERATOR_PATH)], check=True)


def test_batch_enqueue_reuses_the_bounded_staging_classifier() -> None:
    batch = _function("enqueue_batch", "batch_status")
    assert "stage_job_payload \"$1\" root_tmp" in batch
    assert 'mktemp -p "$STAGING"' not in batch
    assert 'mktemp -p "$INTAKE" batch.XXXXXXXX.json 2>/dev/null' in batch
    assert "UV_INTAKE_CONTRACT_INVALID" in batch
    assert "UV_INTAKE_IO_FAILED" in batch
    assert "UV_INTAKE_EXECUTION_FAILED" in batch
    assert "trap \"$cleanup_cmd\" EXIT" in batch
    assert 'rm -f -- "$root_tmp" "$request_tmp" 2>/dev/null || true' in batch


def test_server_intake_rejection_deletes_payload_before_return() -> None:
    submit = _function("submit_drive", "status")
    failure_start = submit.index('if ! intake_output=')
    cleanup_gate = submit.index('if ! rm -f -- "$tmp" 2>/dev/null', failure_start)
    rejection = submit[failure_start:cleanup_gate]
    cleanup_at = rejection.index('rm -f -- "$tmp" 2>/dev/null || true')
    reject_at = rejection.index('intake_reject "$intake_code"')
    return_at = rejection.index("return 1")
    assert cleanup_at < reject_at < return_at
    assert "tmp=''" in rejection
    assert "trap - EXIT" in rejection


def test_server_intake_success_is_not_published_before_confirmed_cleanup() -> None:
    submit = _function("submit_drive", "status")
    cleanup_gate = submit.index('if ! rm -f -- "$tmp" 2>/dev/null')
    cleanup_reject = submit.index("intake_reject 'UV_INTAKE_CLEANUP_FAILED'", cleanup_gate)
    clear_trap = submit.index("trap - EXIT", cleanup_reject)
    publish = submit.index("printf '%s\\n' \"$intake_output\"", clear_trap)
    assert cleanup_gate < cleanup_reject < clear_trap < publish
    assert '[[ -e "$tmp" || -L "$tmp" ]]' in submit


def test_exit_trap_captures_the_actual_staged_path() -> None:
    submit = _function("submit_drive", "status")
    assert "printf -v cleanup_cmd 'rm -f -- %q' \"$tmp\"" in submit
    assert "trap \"$cleanup_cmd\" EXIT" in submit
    assert "${tmp:-}" not in submit


def test_invalid_stage_receipt_cannot_leave_a_reported_path() -> None:
    staging = _function("stage_job_payload", "submit_drive")
    invalid_receipt = staging[staging.index('if [[ -z "$stage_path"') :]
    assert 'rm -f -- "$stage_path" 2>/dev/null || true' in invalid_receipt
    assert "UV_INTAKE_EXECUTION_FAILED" in invalid_receipt
