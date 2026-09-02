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
