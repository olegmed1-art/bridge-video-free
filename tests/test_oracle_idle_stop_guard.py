from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "oracle_idle_state.sh"
SCHEMA = ROOT / "assistant_lab" / "oracle_idle_schema.sql"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run_guard(tmp_path: Path, *, python_output: str = "IDLE:jobs=0,research=0,control=0,video=0", lease: str | None = None, video_dsn: str | None = "postgres://video", observer_busy: bool = False) -> str:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_executable(bindir / "systemctl", "#!/bin/sh\necho active\n")
    _write_executable(bindir / "pgrep", "#!/bin/sh\nexit %d\n" % (0 if observer_busy else 1))
    fake_python = tmp_path / "python"
    _write_executable(fake_python, "#!/bin/sh\nprintf '%s\\n' \"$ORACLE_IDLE_TEST_RESULT\"\n")

    env_file = tmp_path / "assistant-lab.env"
    env_file.write_text("ASSISTANT_LAB_DATABASE_URL=postgres://assistant-lab\n", encoding="utf-8")
    queue_file = tmp_path / "video-queue-dsn"
    if video_dsn is not None:
        queue_file.write_text(video_dsn, encoding="utf-8")
    lease_file = tmp_path / "oracle-host-lease"
    if lease is not None:
        lease_file.write_text(lease, encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env.get('PATH', '')}",
            "ASSISTANT_LAB_ENV_FILE": str(env_file),
            "ASSISTANT_LAB_PYTHON": str(fake_python),
            "BRIDGE_VIDEO_QUEUE_DSN_FILE": str(queue_file),
            "ORACLE_HOST_LEASE_FILE": str(lease_file),
            "ORACLE_IDLE_TEST_RESULT": python_output,
        }
    )
    return subprocess.run(
        ["bash", str(SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


def test_schema_counts_every_nonterminal_research_stage() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "status IN ('QUEUED', 'RUNNING')" in sql
    assert "stage IN ('QUEUED', 'ACCEPTED', 'RUNNING', 'CHECKPOINTED', 'VALIDATING')" in sql
    assert "assistant_lab.control_command" in sql


def test_idle_requires_all_database_families_idle(tmp_path: Path) -> None:
    out = _run_guard(tmp_path)
    assert "ORACLE_IDLE_STATE=IDLE" in out


def test_active_database_family_is_busy(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, python_output="BUSY:jobs=0,research=1,control=0,video=0")
    assert "ORACLE_IDLE_STATE=BUSY" in out


def test_missing_video_telemetry_is_unknown(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, video_dsn=None)
    assert "ORACLE_IDLE_REASON=video_queue_dsn_unavailable" in out
    assert "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_observer_experiment_is_busy(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, observer_busy=True)
    assert "ORACLE_IDLE_REASON=observer_experiment_process" in out
    assert "ORACLE_IDLE_STATE=BUSY" in out


def test_live_operator_lease_is_busy(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, lease="expires_at_epoch=4102444800\n")
    assert "ORACLE_IDLE_REASON=host_lease_active" in out
    assert "ORACLE_IDLE_STATE=BUSY" in out


def test_stale_operator_lease_fails_closed(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, lease="expires_at_epoch=1000000000\n")
    assert "ORACLE_IDLE_REASON=host_lease_stale" in out
    assert "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_malformed_operator_lease_fails_closed(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, lease="not-a-lease\n")
    assert "ORACLE_IDLE_REASON=host_lease_invalid" in out
    assert "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_database_failure_is_unknown(tmp_path: Path) -> None:
    out = _run_guard(tmp_path, python_output="UNKNOWN:database_check_failed")
    assert "ORACLE_IDLE_REASON=database_check_failed" in out
    assert "ORACLE_IDLE_STATE=UNKNOWN" in out
