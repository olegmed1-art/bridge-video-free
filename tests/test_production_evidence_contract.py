#!/usr/bin/env python3
"""Permanent production guard for the evidence-preserving 3.1 FREE r25.6 route."""
from pathlib import Path
import os

import bridge_runtime_hardening_r25_6 as runtime
import run_master_3_1_free as base


def test_production_route_is_confirmed_r25_6():
    adapter = Path("run_drive_3_1_free_generic.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
    assert "bridge_runtime_hardening_r25_6" in adapter
    assert "bridge_runtime_hardening_r25_9" not in adapter
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r25.6"' in workflow
    assert "WHISPER_MODEL: small" in workflow
    assert "BRIDGE_REQUESTED_WHISPER_MODEL: medium" not in workflow


def test_runtime_does_not_filter_master_canon_evidence():
    raw_candidate_builder = base.course_link_candidates
    previous = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = runtime.REVISION
    try:
        runtime.install(lambda: "test-token")
    finally:
        if previous is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = previous
    assert base.course_link_candidates is raw_candidate_builder, (
        "production runtime replaced the master canon-link producer; "
        "deduplication must remain PDF-only"
    )


def test_terminal_preflight_matches_r25_6_receipt_contract():
    source = Path("check_completed_job.py").read_text(encoding="utf-8")
    assert "receipt_matches_revision" in source
    assert "knowledge_status_matches_revision" not in source
    assert "CLEANUP_ACK" in source


if __name__ == "__main__":
    test_production_route_is_confirmed_r25_6()
    test_runtime_does_not_filter_master_canon_evidence()
    test_terminal_preflight_matches_r25_6_receipt_contract()
    print("PRODUCTION_R25_6_EVIDENCE_CONTRACT: PASS")
