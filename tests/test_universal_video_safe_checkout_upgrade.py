from pathlib import Path


def test_rollout_preserves_dirty_checkout_without_reset_or_clean() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "ops/oracle_universal_video_run_command.sh").read_text(encoding="utf-8")
    assert "mktemp -d" in script
    assert "PRESERVED_DIRTY" in script
    assert "mv \"$SOURCE_DIR\" \"$OLD_DIR\"" in script
    assert "fresh staged source checkout is unexpectedly dirty" in script
    assert "git reset --hard" not in script
    assert "git clean" not in script


def test_rollout_refuses_source_swap_while_video_job_is_running() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "ops/oracle_universal_video_run_command.sh").read_text(encoding="utf-8")
    assert "universal-video has a running job; refusing source upgrade" in script


def test_service_does_not_dirty_source_with_import_bytecode() -> None:
    root = Path(__file__).resolve().parents[1]
    unit = (root / "deploy/oracle-universal-video/universal-video.service").read_text(encoding="utf-8")
    assert "Environment=PYTHONDONTWRITEBYTECODE=1" in unit
