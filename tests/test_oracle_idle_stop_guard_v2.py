from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ops"))

from oracle_idle_guard import REQUIRED_FAMILIES, classify  # noqa: E402
from oracle_stop_consumer import StopBlocked, maybe_stop  # noqa: E402

NOW = 2_000_000_000.0


def idle_snapshot() -> dict:
    return {
        "schema": "oracle-idle-telemetry-v1",
        "generated_at": NOW,
        "max_age_seconds": 120,
        "families": {
            name: {"state": "IDLE", "observed_at": NOW}
            for name in REQUIRED_FAMILIES
        },
    }


def test_complete_fresh_idle_is_the_only_idle_verdict() -> None:
    verdict = classify(idle_snapshot(), now=NOW)
    assert verdict.state == "IDLE"
    assert verdict.stop_allowed is True


@pytest.mark.parametrize("family", REQUIRED_FAMILIES)
def test_every_workload_family_busy_blocks_stop(family: str) -> None:
    snapshot = idle_snapshot()
    snapshot["families"][family] = {"state": "BUSY", "observed_at": NOW}
    verdict = classify(snapshot, now=NOW)
    assert verdict.state == "BUSY"
    assert verdict.stop_allowed is False


def test_job_family_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["assistant_lab_job"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_control_command_family_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["assistant_lab_control_command"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_research_parent_family_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["assistant_lab_research_job"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_research_child_family_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["assistant_lab_research_children"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_universal_video_neon_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["universal_video_neon"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_universal_video_spool_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["universal_video_spool"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_universal_video_resident_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["universal_video_resident"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_ben_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["ben"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_bulk_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["bulk"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_other_allowed_workload_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["other_allowed_workloads"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


def test_operator_maintenance_lease_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["operator_maintenance_lease"]["state"] = "BUSY"
    assert classify(snapshot, now=NOW).state == "BUSY"


@pytest.mark.parametrize("family", REQUIRED_FAMILIES)
def test_missing_required_telemetry_is_unknown(family: str) -> None:
    snapshot = idle_snapshot()
    del snapshot["families"][family]
    verdict = classify(snapshot, now=NOW)
    assert verdict.state == "UNKNOWN"
    assert verdict.stop_allowed is False


@pytest.mark.parametrize("family", REQUIRED_FAMILIES)
def test_stale_family_telemetry_is_unknown(family: str) -> None:
    snapshot = idle_snapshot()
    snapshot["families"][family]["observed_at"] = NOW - 121
    verdict = classify(snapshot, now=NOW)
    assert verdict.state == "UNKNOWN"
    assert verdict.stop_allowed is False


def test_stale_whole_snapshot_is_unknown() -> None:
    snapshot = idle_snapshot()
    snapshot["generated_at"] = NOW - 121
    assert classify(snapshot, now=NOW).state == "UNKNOWN"


@pytest.mark.parametrize("family", REQUIRED_FAMILIES)
def test_conflicting_telemetry_is_unknown(family: str) -> None:
    snapshot = idle_snapshot()
    snapshot["families"][family] = {
        "state": "IDLE",
        "observed_at": NOW,
        "signals": [{"state": "IDLE"}, {"state": "BUSY"}],
    }
    verdict = classify(snapshot, now=NOW)
    assert verdict.state == "UNKNOWN"
    assert verdict.stop_allowed is False


def test_unknown_source_blocks_even_when_another_source_is_busy() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["assistant_lab_job"]["state"] = "BUSY"
    snapshot["families"]["universal_video_neon"]["state"] = "UNKNOWN"
    assert classify(snapshot, now=NOW).state == "UNKNOWN"


def test_unexpected_source_is_unknown_not_ignored() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["new_unregistered_workload"] = {
        "state": "IDLE",
        "observed_at": NOW,
    }
    assert classify(snapshot, now=NOW).state == "UNKNOWN"


def test_unknown_never_invokes_stop() -> None:
    snapshot = idle_snapshot()
    del snapshot["families"]["universal_video_neon"]
    calls: list[str] = []
    with pytest.raises(StopBlocked):
        maybe_stop(snapshot, lambda: calls.append("STOP"), now=NOW)
    assert calls == []


def test_busy_never_invokes_stop() -> None:
    snapshot = idle_snapshot()
    snapshot["families"]["assistant_lab_control_command"]["state"] = "BUSY"
    calls: list[str] = []
    with pytest.raises(StopBlocked):
        maybe_stop(snapshot, lambda: calls.append("STOP"), now=NOW)
    assert calls == []


def test_only_proven_idle_invokes_stop_once() -> None:
    calls: list[str] = []
    verdict = maybe_stop(idle_snapshot(), lambda: calls.append("STOP"), now=NOW)
    assert verdict.state == "IDLE"
    assert calls == ["STOP"]


def test_sql_snapshot_counts_all_nonterminal_research_stages_and_children() -> None:
    sql = (ROOT / "assistant_lab" / "oracle_idle_schema.sql").read_text(encoding="utf-8")
    for stage in ("QUEUED", "ACCEPTED", "RUNNING", "CHECKPOINTED", "VALIDATING"):
        assert stage in sql
    assert "active_research_children" in sql
    assert "active_ben_jobs" in sql
    assert "active_bulk_jobs" in sql
    assert "active_other_jobs" in sql
    assert "stale_running_jobs" in sql
    assert "heartbeat_at" in sql
    assert "p_stale_after_seconds" in sql
    assert "clock_timestamp()" in sql


def test_video_queue_nonterminal_states_are_explicit_in_collector() -> None:
    source = (ROOT / "ops" / "oracle_idle_collect.py").read_text(encoding="utf-8")
    for status in ("PENDING_CANARY", "QUEUED", "LEASED"):
        assert status in source


def test_collector_has_local_spool_resident_and_lease_sources() -> None:
    source = (ROOT / "ops" / "oracle_idle_collect.py").read_text(encoding="utf-8")
    assert 'for name in ("inbox", "running")' in source
    assert "assistant-lab.service" in source
    assert "universal-video.service" in source
    assert "universal-video-container.service" in source
    assert "universal-video-resident-status-v2" in source
    assert "assistant_lab_stale_running_heartbeat" in source
    assert "operator_lease_stale" in source


def test_stop_consumer_contains_no_oci_or_power_command() -> None:
    source = (ROOT / "ops" / "oracle_stop_consumer.py").read_text(encoding="utf-8").lower()
    assert "oci compute" not in source
    assert "instance action" not in source
    assert "subprocess" not in source
