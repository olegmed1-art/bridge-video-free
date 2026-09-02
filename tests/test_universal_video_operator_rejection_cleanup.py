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
    assert 'cleanup_staged_files "$root_tmp" "$request_tmp"' in batch
    assert "UV_INTAKE_CLEANUP_FAILED" in batch
    assert '\n    rm -f -- "$root_tmp"' not in batch
    assert '\n    rm -f -- "$root_tmp" "$request_tmp"' not in batch
    queue_call = batch.index('queue_output="$(runuser -u universal-video')
    cleanup_gate = batch.index(
        'if ! cleanup_staged_files "$root_tmp" "$request_tmp"', queue_call
    )
    publish = batch.index("printf '%s\\n' \"$queue_output\"", cleanup_gate)
    assert queue_call < cleanup_gate < publish


def test_server_intake_rejection_deletes_payload_before_return() -> None:
    submit = _function("submit_drive", "status")
    failure_start = submit.index('if ! intake_output=')
    cleanup_at = submit.index('if ! cleanup_staged_files "$tmp"', failure_start)
    cleanup_reject_at = submit.index(
        "intake_reject 'UV_INTAKE_CLEANUP_FAILED'", cleanup_at
    )
    cleanup_failure_return = submit.index("return 1", cleanup_reject_at)
    clear_path = submit.index("tmp=''", cleanup_failure_return)
    clear_trap = submit.index("trap - EXIT", clear_path)
    reject_at = submit.index('intake_reject "$intake_code"', clear_trap)
    rejection_return = submit.index("return 1", reject_at)
    assert (
        failure_start
        < cleanup_at
        < cleanup_reject_at
        < cleanup_failure_return
        < clear_path
        < clear_trap
        < reject_at
        < rejection_return
    )


def test_server_intake_success_is_not_published_before_confirmed_cleanup() -> None:
    submit = _function("submit_drive", "status")
    rejection_cleanup = submit.index('if ! cleanup_staged_files "$tmp"')
    cleanup_gate = submit.index(
        'if ! cleanup_staged_files "$tmp"', rejection_cleanup + 1
    )
    cleanup_reject = submit.index("intake_reject 'UV_INTAKE_CLEANUP_FAILED'", cleanup_gate)
    clear_trap = submit.index("trap - EXIT", cleanup_reject)
    publish = submit.index("printf '%s\\n' \"$intake_output\"", clear_trap)
    assert cleanup_gate < cleanup_reject < clear_trap < publish


def test_exit_trap_captures_the_actual_staged_path() -> None:
    submit = _function("submit_drive", "status")
    assert (
        "printf -v cleanup_cmd 'rm -f -- %q 2>/dev/null || true' \"$tmp\""
        in submit
    )
    assert "trap \"$cleanup_cmd\" EXIT" in submit
    assert "${tmp:-}" not in submit


def test_cleanup_fallback_traps_are_silent() -> None:
    batch = _function("enqueue_batch", "batch_status")
    submit = _function("submit_drive", "status")
    assert "printf -v cleanup_cmd 'rm -f -- %q 2>/dev/null || true'" in batch
    assert "printf -v cleanup_cmd 'rm -f -- %q %q 2>/dev/null || true'" in batch
    assert "printf -v cleanup_cmd 'rm -f -- %q 2>/dev/null || true'" in submit


def test_invalid_stage_receipt_cannot_leave_a_reported_path() -> None:
    staging = _function("stage_job_payload", "submit_drive")
    invalid_receipt = staging[staging.index('if [[ -z "$stage_path"') :]
    assert 'cleanup_staged_files "$stage_path"' in invalid_receipt
    assert "UV_INTAKE_CLEANUP_FAILED" in invalid_receipt
    assert "UV_INTAKE_EXECUTION_FAILED" in invalid_receipt


def test_python_staging_cleanup_failure_has_a_bounded_code() -> None:
    staging = _function("stage_job_payload", "submit_drive")
    python_start = staging.index("def cleanup_staged(candidate):")
    python_end = staging.index("')\"", python_start)
    python_body = staging[python_start:python_end]
    assert "except FileNotFoundError:" in python_body
    assert "except OSError:" in python_body
    assert "UV_ERROR_CODE=UV_INTAKE_CLEANUP_FAILED" in python_body
    assert "pass\n    print" not in python_body


def test_cleanup_helper_requires_confirmed_absence() -> None:
    cleanup = _function("cleanup_staged_files", "verify")
    assert 'rm -f -- "$path" 2>/dev/null || return 1' in cleanup
    assert '[[ ! -e "$path" && ! -L "$path" ]] || return 1' in cleanup


def test_batch_success_cannot_be_returned_before_confirmed_cleanup() -> None:
    batch = _function("enqueue_batch", "batch_status")
    queue_result = batch.index("queue_rc=$?")
    cleanup_gate = batch.index(
        'if ! cleanup_staged_files "$root_tmp" "$request_tmp"', queue_result
    )
    cleanup_reject = batch.index(
        "intake_reject 'UV_INTAKE_CLEANUP_FAILED'", cleanup_gate
    )
    clear_trap = batch.index("trap - EXIT", cleanup_reject)
    return_result = batch.index('return "$queue_rc"', clear_trap)
    assert queue_result < cleanup_gate < cleanup_reject < clear_trap < return_result
