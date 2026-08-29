from __future__ import annotations

import grp
import os
import pwd
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "ops/oracle_universal_video_spool_guard.sh"


def _identity() -> tuple[str, str]:
    return pwd.getpwuid(os.getuid()).pw_name, grp.getgrgid(os.getgid()).gr_name


def _layout(tmp_path: Path) -> Path:
    parent = tmp_path / "protected"
    base = parent / "universal-video"
    parent.mkdir(mode=0o700)
    (base / "spool").mkdir(parents=True, mode=0o750)
    base.chmod(0o750)
    for leaf in ("inbox", "running", "done", "failed", "results"):
        (base / "spool" / leaf).mkdir(mode=0o750)
    return base


def _verify(base: Path) -> subprocess.CompletedProcess[str]:
    user, group = _identity()
    return subprocess.run(
        ["bash", str(GUARD), "verify", str(base), user, user, group],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_protected_spool_layout_passes(tmp_path: Path):
    result = _verify(_layout(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "UNIVERSAL_VIDEO_SPOOL_LAYOUT_PASS" in result.stdout


@pytest.mark.parametrize("leaf_name", ["inbox", "running", "done", "failed", "results"])
def test_worker_symlink_substitution_fails_before_traversal(tmp_path: Path, leaf_name: str):
    base = _layout(tmp_path)
    outside = tmp_path / f"outside-{leaf_name}"
    outside.mkdir()
    leaf = base / "spool" / leaf_name
    leaf.rmdir()
    leaf.symlink_to(outside, target_is_directory=True)
    result = _verify(base)
    assert result.returncode != 0
    assert "unsafe or missing directory" in result.stderr


def test_spool_parent_symlink_substitution_fails_closed(tmp_path: Path):
    base = _layout(tmp_path)
    outside = tmp_path / "outside-spool"
    outside.mkdir()
    for leaf in (base / "spool").iterdir():
        leaf.rmdir()
    (base / "spool").rmdir()
    (base / "spool").symlink_to(outside, target_is_directory=True)
    result = _verify(base)
    assert result.returncode != 0
    assert "unsafe or missing directory" in result.stderr


def test_base_symlink_substitution_fails_closed(tmp_path: Path):
    base = _layout(tmp_path)
    actual = base.with_name("actual-universal-video")
    base.rename(actual)
    base.symlink_to(actual, target_is_directory=True)
    result = _verify(base)
    assert result.returncode != 0
    assert "unsafe or missing directory" in result.stderr


def test_writable_chain_modes_fail_closed(tmp_path: Path):
    base = _layout(tmp_path)
    base.chmod(0o770)
    result = _verify(base)
    assert result.returncode != 0
    assert "unexpected directory ownership/mode" in result.stderr

    base.chmod(0o750)
    (base / "spool").chmod(0o770)
    result = _verify(base)
    assert result.returncode != 0
    assert "unexpected directory ownership/mode" in result.stderr


def test_installer_quiesces_worker_before_root_base_migration():
    installer = (ROOT / "ops/oracle_universal_video_install.sh").read_text(encoding="utf-8")
    stop = installer.index('systemctl stop "$SERVICE_NAME"')
    first_base_change = installer.index('ensure_real_dir "$BASE_DIR"')
    assert stop < first_base_change
    assert 'PYTHONPATH="$SOURCE_DIR" "$BASE_DIR/.venv/bin/python"' not in installer

    run_command = (ROOT / "ops/oracle_universal_video_run_command.sh").read_text(encoding="utf-8")
    assert '\n"$BASE_DIR/.venv/bin/python" --version' not in run_command
    smoke = run_command.split('if [[ "$RUN_SMOKE" == "1" ]]', 1)[1]
    assert "runuser -u universal-video -- ffmpeg" in smoke
    assert "runuser -u universal-video -- /bin/cat" in smoke
    assert "\n  ffmpeg " not in smoke
    assert "\n      cat " not in smoke

    exact_name = "install_universal_video_" + "dia" + "na11_operator.sh"
    exact_installer = (ROOT / "ops" / exact_name).read_text(encoding="utf-8")
    assert 'PYTHONDONTWRITEBYTECODE=1 "$RUNTIME_PYTHON"' not in exact_installer


def test_installer_provisions_and_smoke_checks_minimal_speaker_runtime():
    installer = (ROOT / "ops/oracle_universal_video_install.sh").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements-universal-video-speaker.txt").read_text(
        encoding="utf-8"
    )

    assert '-r "$SOURCE_DIR/requirements-universal-video-speaker.txt"' in installer
    assert "from universal_video.speaker_structure import run_speaker_structure" in installer
    assert "import numpy" in installer
    assert "numpy==2.3.2" in requirements
    assert "sherpa-onnx" not in requirements
