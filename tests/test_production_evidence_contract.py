#!/usr/bin/env python3
"""Permanent production guard for evidence-preserving 3.1 FREE r25.11."""
from pathlib import Path
import os

import bridge_runtime_hardening_r25_11 as runtime
import run_master_3_1_free as base


def test_production_route_is_confirmed_r25_11():
    adapter = Path("run_drive_3_1_free_generic.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
    assert "bridge_runtime_hardening_r25_11" in adapter
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r25.11"' in workflow
    assert "BRIDGE_REQUESTED_WHISPER_MODEL: medium" in workflow
    assert "WHISPER_MODEL: medium" in workflow
    assert "BRIDGE_WORKER_DATABASE_URL" in workflow
    assert "Worker database runtime preflight" in workflow


def test_runtime_does_not_filter_master_canon_evidence():
    raw_candidate_builder = base.course_link_candidates
    previous = {
        key: os.environ.get(key)
        for key in (
            "BRIDGE_REQUESTED_ALGORITHM_REVISION",
            "BRIDGE_REQUESTED_WHISPER_MODEL",
            "WHISPER_MODEL",
        )
    }
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = runtime.REVISION
    os.environ["BRIDGE_REQUESTED_WHISPER_MODEL"] = "medium"
    os.environ["WHISPER_MODEL"] = "medium"
    try:
        runtime.install(lambda: "test-token")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert base.course_link_candidates is raw_candidate_builder, (
        "production runtime replaced the master canon-link producer; "
        "deduplication must remain PDF-only"
    )


def test_terminal_preflight_requires_applied_knowledge():
    source = Path("check_completed_job.py").read_text(encoding="utf-8")
    assert "receipt_matches_revision" in source
    assert "knowledge_status_matches_revision" in source
    assert "KNOWLEDGE_APPLIED" in source
    assert "CLEANUP_ACK" in source


def test_periodic_auto_discovery_remains_disabled():
    source = Path(".github/workflows/bridge-video-auto-discovery.yml").read_text(
        encoding="utf-8"
    )
    assert "schedule:" not in source
    assert "actions: write" not in source
    assert "GOOGLE_DRIVE_OAUTH_JSON" not in source
    assert "discover_next_drive_job.py" not in source
    assert "workflow_dispatch:" in source
    assert "AUTO_DISCOVERY_DISABLED" in source


if __name__ == "__main__":
    test_production_route_is_confirmed_r25_11()
    test_runtime_does_not_filter_master_canon_evidence()
    test_terminal_preflight_requires_applied_knowledge()
    test_periodic_auto_discovery_remains_disabled()
    print("PRODUCTION_R25_11_EVIDENCE_CONTRACT: PASS")
