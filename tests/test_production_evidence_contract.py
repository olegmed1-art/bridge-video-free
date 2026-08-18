#!/usr/bin/env python3
"""Permanent production guard for the evidence-preserving 3.1 FREE r25.14 route.

r25.14 is accepted only when it demonstrably remains a thin descendant of the
proven r25.7 -> r25.6 media/ASR/evidence route and changes only the local
speaker-diarization layer. The guard must not be weakened to bypass an older
revision assertion.
"""
from pathlib import Path
import os

import bridge_runtime_hardening_r25_14 as runtime
import run_master_3_1_free as base


def test_production_route_is_confirmed_r25_14_with_r25_7_and_r25_6_inheritance():
    adapter = Path("run_drive_3_1_free_generic.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
    runtime_source = Path("bridge_runtime_hardening_r25_14.py").read_text(encoding="utf-8")
    r25_7_source = Path("bridge_runtime_hardening_r25_7.py").read_text(encoding="utf-8")
    semantic_v2 = Path("run_master_3_1_free_semantic_v2.py").read_text(encoding="utf-8")
    diarization_v2 = Path("bridge_speaker_diarization_v2.py").read_text(encoding="utf-8")

    assert "bridge_runtime_hardening_r25_14" in adapter
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r25.14"' in workflow
    assert 'BRIDGE_DIARIZATION_ENABLED: "true"' in workflow
    assert "WHISPER_MODEL: small" in workflow
    assert "BRIDGE_REQUESTED_WHISPER_MODEL: medium" not in workflow
    assert 'BRIDGE_PAID_CLOUD: "false"' in workflow
    assert 'BRIDGE_BILLING_FALLBACK: "false"' in workflow

    # r25.14 must inherit r25.7 rather than replace the proven media/ASR path.
    assert "import bridge_runtime_hardening_r25_7 as previous" in runtime_source
    assert "previous.install(token_func)" in runtime_source
    assert "bridge_speaker_diarization_v2" in runtime_source
    # r25.7 itself remains a thin extension of r25.6.
    assert "import bridge_runtime_hardening_r25_6 as previous" in r25_7_source
    assert "previous.install(token_func)" in r25_7_source
    assert "import run_master_3_1_free_semantic as previous" in semantic_v2
    assert "METHODOLOGY_PARTIAL" in semantic_v2
    assert "technical_ready_does_not_imply_methodology_ready" in semantic_v2

    # New diarization is local, anonymous and fail-soft; authority guards remain elsewhere.
    assert "sherpa-onnx" in diarization_v2
    assert "fallback.diarize_transcript" in diarization_v2
    assert '"real_person_identity_claimed": False' in diarization_v2
    assert '"voice_embedding_persisted": False' in diarization_v2
    assert '"cross_lesson_voice_profile_persisted": False' in diarization_v2
    assert '"paid_api": 0' in diarization_v2
    assert '"paid_cloud": 0' in diarization_v2


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
        "retrieval classification must not erase the original evidence set"
    )


def test_terminal_preflight_matches_revision_receipt_contract():
    source = Path("check_completed_job.py").read_text(encoding="utf-8")
    assert "receipt_matches_revision" in source
    assert "knowledge_status_matches_revision" not in source
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
    test_production_route_is_confirmed_r25_14_with_r25_7_and_r25_6_inheritance()
    test_runtime_does_not_filter_master_canon_evidence()
    test_terminal_preflight_matches_revision_receipt_contract()
    test_periodic_auto_discovery_remains_disabled()
    print("PRODUCTION_R25_14_EVIDENCE_CONTRACT: PASS")
