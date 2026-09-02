from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))

import oracle_idle_collect as collect  # noqa: E402


def test_absent_operator_lease_is_observed_idle(tmp_path: Path) -> None:
    with patch.object(collect, "LEASE_FILE", tmp_path / "missing"):
        result = collect._lease(2_000_000_000.0)
    assert result["state"] == "IDLE"
    assert result["evidence"]["lease_present"] is False


def test_stale_operator_lease_is_unknown(tmp_path: Path) -> None:
    lease = tmp_path / "lease"
    lease.write_text("expires_at_epoch=1999999999\n", encoding="utf-8")
    with patch.object(collect, "LEASE_FILE", lease):
        result = collect._lease(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"
    assert result["evidence"]["reason"] == "operator_lease_stale"


def test_live_operator_lease_is_busy(tmp_path: Path) -> None:
    lease = tmp_path / "lease"
    lease.write_text("expires_at_epoch=2000000100\n", encoding="utf-8")
    with patch.object(collect, "LEASE_FILE", lease):
        result = collect._lease(2_000_000_000.0)
    assert result["state"] == "BUSY"


def test_missing_video_queue_telemetry_is_unknown(tmp_path: Path) -> None:
    with patch.object(collect, "VIDEO_DSN", tmp_path / "missing"):
        result = collect._video_neon(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"


def test_local_spool_running_or_inbox_is_busy(tmp_path: Path) -> None:
    for name in ("inbox", "running"):
        (tmp_path / name).mkdir()
    (tmp_path / "running" / "job.json").write_text("{}", encoding="utf-8")
    with patch.object(collect, "UV_SPOOL", tmp_path):
        result = collect._spool(2_000_000_000.0)
    assert result["state"] == "BUSY"


def test_missing_spool_is_unknown(tmp_path: Path) -> None:
    with patch.object(collect, "UV_SPOOL", tmp_path / "missing"):
        result = collect._spool(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"


def test_active_resident_requires_fresh_status(tmp_path: Path) -> None:
    status = tmp_path / "status.json"
    with patch.object(collect, "UV_STATUS", status), patch.object(
        collect, "_systemctl_state", side_effect=["active", "inactive"]
    ):
        result = collect._resident(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"


def test_resident_status_with_active_jobs_is_busy(tmp_path: Path) -> None:
    status = tmp_path / "status.json"
    status.write_text(
        json.dumps(
            {
                "schema": "universal-video-resident-status-v2",
                "observed_at_unix": 2_000_000_000.0,
                "active_jobs": ["job-1"],
            }
        ),
        encoding="utf-8",
    )
    with patch.object(collect, "UV_STATUS", status), patch.object(
        collect, "_systemctl_state", side_effect=["active", "inactive"]
    ):
        result = collect._resident(2_000_000_000.0)
    assert result["state"] == "BUSY"


def test_failed_resident_service_is_unknown() -> None:
    with patch.object(collect, "_systemctl_state", side_effect=["failed", "inactive"]):
        result = collect._resident(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"


def test_assistant_lab_resident_active_is_idle_source() -> None:
    with patch.object(collect, "_systemctl_state", return_value="active"):
        result = collect._assistant_resident(2_000_000_000.0)
    assert result["state"] == "IDLE"


def test_assistant_lab_resident_inactive_is_unknown() -> None:
    with patch.object(collect, "_systemctl_state", return_value="inactive"):
        result = collect._assistant_resident(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"


def _observer_tree(root: Path) -> None:
    for relative in ("jobs/pending", "jobs/running", "work"):
        (root / relative).mkdir(parents=True, exist_ok=True)


def test_observer_pending_queue_is_busy_primary_evidence(tmp_path: Path) -> None:
    _observer_tree(tmp_path)
    (tmp_path / "jobs/pending/exp.json").write_text("{}", encoding="utf-8")
    with patch.object(collect, "OBSERVER_ROOT", tmp_path), patch.object(
        collect, "_systemctl_state", return_value="inactive"
    ):
        result = collect._observer_workload(2_000_000_000.0)
    assert result["state"] == "BUSY"


def test_observer_running_queue_is_busy_primary_evidence(tmp_path: Path) -> None:
    _observer_tree(tmp_path)
    (tmp_path / "jobs/running/exp.json").write_text("{}", encoding="utf-8")
    with patch.object(collect, "OBSERVER_ROOT", tmp_path):
        result = collect._observer_workload(2_000_000_000.0)
    assert result["state"] == "BUSY"


def test_observer_work_directory_is_busy_primary_evidence(tmp_path: Path) -> None:
    _observer_tree(tmp_path)
    (tmp_path / "work/EXP-1").mkdir()
    with patch.object(collect, "OBSERVER_ROOT", tmp_path):
        result = collect._observer_workload(2_000_000_000.0)
    assert result["state"] == "BUSY"


def test_observer_missing_queue_is_unknown(tmp_path: Path) -> None:
    with patch.object(collect, "OBSERVER_ROOT", tmp_path):
        result = collect._observer_workload(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"


def test_observer_empty_queue_requires_active_daemon(tmp_path: Path) -> None:
    _observer_tree(tmp_path)
    with patch.object(collect, "OBSERVER_ROOT", tmp_path), patch.object(
        collect, "_systemctl_state", return_value="active"
    ):
        result = collect._observer_workload(2_000_000_000.0)
    assert result["state"] == "IDLE"


def test_observer_external_family_is_busy() -> None:
    with patch.object(collect, "_observer_workload", return_value={"state": "BUSY", "observed_at": 2_000_000_000.0}), patch.object(
        collect, "_process_match", side_effect=[False, False]
    ):
        result = collect._external_processes(2_000_000_000.0)
    assert result["state"] == "BUSY"
    assert "observer" in result["evidence"]["active_process_families"]


def test_ben_external_process_is_busy() -> None:
    with patch.object(collect, "_observer_workload", return_value={"state": "IDLE", "observed_at": 2_000_000_000.0}), patch.object(
        collect, "_process_match", side_effect=[True, False]
    ):
        result = collect._external_processes(2_000_000_000.0)
    assert result["state"] == "BUSY"
    assert "ben" in result["evidence"]["active_process_families"]


def test_bulk_external_process_is_busy() -> None:
    with patch.object(collect, "_observer_workload", return_value={"state": "IDLE", "observed_at": 2_000_000_000.0}), patch.object(
        collect, "_process_match", side_effect=[False, True]
    ):
        result = collect._external_processes(2_000_000_000.0)
    assert result["state"] == "BUSY"
    assert "bulk" in result["evidence"]["active_process_families"]


def test_process_telemetry_failure_is_unknown() -> None:
    with patch.object(collect, "_observer_workload", return_value={"state": "IDLE", "observed_at": 2_000_000_000.0}), patch.object(
        collect, "_process_match", return_value=None
    ):
        result = collect._external_processes(2_000_000_000.0)
    assert result["state"] == "UNKNOWN"
