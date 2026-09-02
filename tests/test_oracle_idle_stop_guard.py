from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLASSIFIER = ROOT / "ops" / "oracle_idle_state.sh"
STOP_GUARD = ROOT / "ops" / "oracle_stop_guard.sh"
SCHEMA = ROOT / "assistant_lab" / "oracle_idle_schema.sql"


def _exe(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fake_psycopg(path: Path) -> None:
    path.write_text(
        """import json
import os

class Cursor:
    def __init__(self, app):
        self.app = app
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def execute(self, query):
        self.query = query
    def fetchone(self):
        fail = os.environ.get("ORACLE_TEST_DB_FAIL", "")
        if self.app == "oracle-idle-state":
            if fail == "assistant":
                raise RuntimeError("assistant failure")
            return json.loads(os.environ["ORACLE_TEST_CORE_ROW"])
        if fail == "video":
            raise RuntimeError("video failure")
        return json.loads(os.environ["ORACLE_TEST_VIDEO_ROW"])

class Connection:
    def __init__(self, app):
        self.app = app
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def cursor(self):
        return Cursor(self.app)

def connect(dsn, connect_timeout, application_name):
    return Connection(application_name)
""",
        encoding="utf-8",
    )


def _run_classifier(
    tmp_path: Path,
    *,
    core_counts: tuple[int, int, int, int] = (0, 0, 0, 0),
    core_observed: int | None = None,
    video_count: int = 0,
    video_observed: int | None = None,
    db_fail: str = "",
    queue_dsn: bool = True,
    generic_spool_present: bool = True,
    generic_spool_work: bool = False,
    video_leaf: str | None = None,
    progress_state: str = "PROCESSING",
    progress_observed: int | None = None,
    video_spool_present: bool = True,
    lease_purpose: str | None = None,
    lease_issued: int | None = None,
    lease_expires: int | None = None,
) -> str:
    now = int(time.time())
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True)
    _exe(bindir / "systemctl", "#!/bin/sh\necho active\nexit 0\n")
    _exe(bindir / "pgrep", "#!/bin/sh\nexit 1\n")

    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    _fake_psycopg(module_dir / "psycopg.py")
    env_file = tmp_path / "assistant-lab.env"
    env_file.write_text("ASSISTANT_LAB_DATABASE_URL=postgres://assistant\n", encoding="utf-8")
    queue_file = tmp_path / "video-queue-dsn"
    if queue_dsn:
        queue_file.write_text("postgres://video\n", encoding="utf-8")

    generic = tmp_path / "generic-spool"
    if generic_spool_present:
        generic.mkdir()
    if generic_spool_work:
        (generic / "work.json").write_text("{}", encoding="utf-8")

    video = tmp_path / "video-spool"
    if video_spool_present:
        for leaf in ("inbox", "running", "progress", "done", "failed", "results"):
            (video / leaf).mkdir(parents=True, exist_ok=True)
        if video_leaf:
            target = video / video_leaf / "work.json"
            if video_leaf == "progress":
                target.write_text(
                    json.dumps(
                        {
                            "schema": "universal-video-pipeline-progress-v1",
                            "state": progress_state,
                            "observed_at_unix": progress_observed if progress_observed is not None else now,
                        }
                    ),
                    encoding="utf-8",
                )
            else:
                target.write_text("{}", encoding="utf-8")

    lease_dir = tmp_path / "lease"
    lease_dir.mkdir()
    lease = lease_dir / "oracle-host-lease"
    if lease_purpose:
        issued = lease_issued if lease_issued is not None else now - 1
        expires = lease_expires if lease_expires is not None else now + 300
        lease.write_text(
            "schema=oracle-host-lease-v1\n"
            f"purpose={lease_purpose}\n"
            f"issued_at_epoch={issued}\n"
            f"expires_at_epoch={expires}\n",
            encoding="utf-8",
        )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bindir}:{env.get('PATH', '')}",
            "PYTHONPATH": f"{module_dir}:{env.get('PYTHONPATH', '')}",
            "ASSISTANT_LAB_PYTHON": sys.executable,
            "ASSISTANT_LAB_ENV_FILE": str(env_file),
            "BRIDGE_VIDEO_QUEUE_DSN_FILE": str(queue_file),
            "BRIDGE_VIDEO_SPOOL_ROOT": str(video),
            "ORACLE_IDLE_SPOOL_PATHS": str(generic),
            "ORACLE_HOST_LEASE_FILE": str(lease),
            "ORACLE_TEST_CORE_ROW": json.dumps(
                ["assistant-lab-oracle-idle-v2", core_observed if core_observed is not None else now, *core_counts]
            ),
            "ORACLE_TEST_VIDEO_ROW": json.dumps(
                ["universal-video-idle-v1", video_observed if video_observed is not None else now, video_count]
            ),
            "ORACLE_TEST_DB_FAIL": db_fail,
        }
    )
    return subprocess.run(
        ["bash", str(CLASSIFIER)], check=True, capture_output=True, text=True, env=env
    ).stdout


def _run_stop_guard(tmp_path: Path, state: str, *, rc: int = 0, extra: str = "") -> subprocess.CompletedProcess[str]:
    probe = tmp_path / "probe"
    _exe(
        probe,
        "#!/bin/sh\n"
        f"printf '{extra}'\n"
        "printf 'ORACLE_IDLE_REASON=test\\n'\n"
        f"printf 'ORACLE_IDLE_STATE={state}\\n'\n"
        f"exit {rc}\n",
    )
    env = os.environ.copy()
    env["ORACLE_IDLE_PROBE"] = str(probe)
    return subprocess.run(["bash", str(STOP_GUARD)], capture_output=True, text=True, env=env)


def test_schema_covers_job_research_child_and_control_families() -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    assert "status IN ('QUEUED', 'CLAIMED', 'RUNNING')" in sql
    assert "stage NOT IN ('COMPLETED', 'FAILED', 'CANCELLED')" in sql
    assert "JOIN assistant_lab.job AS j ON j.job_id = r.child_job_id" in sql
    assert "assistant_lab.control_command" in sql
    assert "observed_at_epoch" in sql
    assert "DROP FUNCTION IF EXISTS assistant_lab.oracle_idle_snapshot()" in sql


def test_assistant_lab_job_forbids_idle(tmp_path: Path) -> None:
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, core_counts=(1, 0, 0, 0))


def test_control_command_forbids_idle(tmp_path: Path) -> None:
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, core_counts=(0, 0, 0, 1))


def test_research_job_forbids_idle(tmp_path: Path) -> None:
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, core_counts=(0, 1, 0, 0))


def test_research_child_forbids_idle(tmp_path: Path) -> None:
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, core_counts=(0, 0, 1, 0))


def test_universal_video_pending_canary_forbids_idle(tmp_path: Path) -> None:
    assert "'PENDING_CANARY'" in CLASSIFIER.read_text(encoding="utf-8")
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, video_count=1)


def test_universal_video_queued_forbids_idle(tmp_path: Path) -> None:
    assert "'QUEUED'" in CLASSIFIER.read_text(encoding="utf-8")
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, video_count=1)


def test_universal_video_leased_forbids_idle(tmp_path: Path) -> None:
    assert "'LEASED'" in CLASSIFIER.read_text(encoding="utf-8")
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, video_count=1)


def test_generic_local_spool_forbids_idle(tmp_path: Path) -> None:
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, generic_spool_work=True)


def test_video_inbox_and_running_forbid_idle(tmp_path: Path) -> None:
    for leaf in ("inbox", "running"):
        case = tmp_path / leaf
        case.mkdir()
        assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(case, video_leaf=leaf)


def test_active_video_progress_forbids_idle(tmp_path: Path) -> None:
    assert "ORACLE_IDLE_STATE=BUSY" in _run_classifier(tmp_path, video_leaf="progress")


def test_terminal_video_progress_does_not_fake_work(tmp_path: Path) -> None:
    for state in ("RESULT_READY", "REVIEW", "FAILED"):
        case = tmp_path / state.lower()
        case.mkdir()
        assert "ORACLE_IDLE_STATE=IDLE" in _run_classifier(case, video_leaf="progress", progress_state=state)


def test_operator_lease_forbids_idle(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, lease_purpose="operator")
    assert "host_lease_active:operator" in out and "ORACLE_IDLE_STATE=BUSY" in out


def test_maintenance_lease_forbids_idle(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, lease_purpose="maintenance")
    assert "host_lease_active:maintenance" in out and "ORACLE_IDLE_STATE=BUSY" in out


def test_stale_database_telemetry_is_unknown(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, core_observed=int(time.time()) - 1000)
    assert "assistant_lab_telemetry_stale" in out and "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_stale_video_progress_is_unknown(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, video_leaf="progress", progress_observed=int(time.time()) - 1000)
    assert "video_spool_progress_stale" in out and "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_stale_lease_is_unknown(tmp_path: Path) -> None:
    now = int(time.time())
    out = _run_classifier(tmp_path, lease_purpose="operator", lease_issued=now - 600, lease_expires=now - 1)
    assert "host_lease_stale" in out and "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_missing_queue_telemetry_is_unknown(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, queue_dsn=False)
    assert "video_queue_dsn_unavailable" in out and "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_missing_generic_spool_is_unknown(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, generic_spool_present=False)
    assert "spool_unavailable:" in out and "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_missing_video_spool_is_unknown(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, video_spool_present=False)
    assert "video_spool_unavailable" in out and "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_partial_success_then_video_failure_is_unknown(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, db_fail="video")
    assert "video_queue_telemetry_unavailable_after_assistant_lab_success" in out
    assert "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_unknown_always_forbids_stop(tmp_path: Path) -> None:
    result = _run_stop_guard(tmp_path, "UNKNOWN")
    assert result.returncode != 0 and "ORACLE_STOP_ALLOWED=NO" in result.stdout


def test_busy_forbids_stop(tmp_path: Path) -> None:
    result = _run_stop_guard(tmp_path, "BUSY")
    assert result.returncode != 0 and "ORACLE_STOP_ALLOWED=NO" in result.stdout


def test_only_exact_idle_authorizes_future_stop_consumer(tmp_path: Path) -> None:
    result = _run_stop_guard(tmp_path, "IDLE")
    assert result.returncode == 0 and "ORACLE_STOP_ALLOWED=YES" in result.stdout


def test_failed_or_extra_output_probe_forbids_stop(tmp_path: Path) -> None:
    failed = _run_stop_guard(tmp_path / "failed", "IDLE", rc=7)
    extra = _run_stop_guard(tmp_path / "extra", "IDLE", extra="noise\\n")
    assert failed.returncode != 0 and extra.returncode != 0
    assert "ORACLE_STOP_ALLOWED=NO" in failed.stdout
    assert "ORACLE_STOP_ALLOWED=NO" in extra.stdout


def test_unknown_precedes_known_busy_when_any_source_is_missing(tmp_path: Path) -> None:
    out = _run_classifier(tmp_path, core_counts=(1, 0, 0, 0), queue_dsn=False)
    assert "ORACLE_IDLE_STATE=UNKNOWN" in out


def test_guard_scripts_contain_no_stop_restart_or_reboot_side_effect() -> None:
    combined = CLASSIFIER.read_text(encoding="utf-8") + STOP_GUARD.read_text(encoding="utf-8")
    forbidden = ("shutdown ", "poweroff", "reboot ", "instance action --action STOP", "systemctl stop")
    assert all(token not in combined for token in forbidden)
