#!/usr/bin/env python3
"""Regression tests for revision-aware idempotency r25.10."""
from pathlib import Path

import bridge_runtime_hardening_r25_10 as runtime
import check_completed_job as preflight


JOB = "86e814014cabee88785a53340ab85666"
CURRENT = "3.1-free-r25.10"


def test_legacy_receipt_does_not_block_new_revision():
    legacy = {"status": "CLEANUP_ACK", "job_id": JOB}
    assert not preflight.receipt_matches_revision(legacy, JOB, CURRENT)
    assert not runtime.receipt_matches_revision(legacy, JOB, CURRENT)


def test_same_revision_receipt_is_idempotent():
    current = {
        "status": "CLEANUP_ACK",
        "job_id": JOB,
        "algorithmRevision": CURRENT,
    }
    assert preflight.receipt_matches_revision(current, JOB, CURRENT)
    assert runtime.receipt_matches_revision(current, JOB, CURRENT)


def test_knowledge_receipt_is_required_and_revision_aware():
    applied = {
        "status": "KNOWLEDGE_APPLIED",
        "job_id": JOB,
        "algorithmRevision": CURRENT,
    }
    not_applied = dict(applied, status="KNOWLEDGE_NOT_APPLIED")
    assert preflight.knowledge_status_matches_revision(applied, JOB, CURRENT)
    assert not preflight.knowledge_status_matches_revision(not_applied, JOB, CURRENT)
    assert not preflight.knowledge_status_matches_revision(
        dict(applied, algorithmRevision="3.1-free-r25.9"), JOB, CURRENT
    )


def test_different_revision_can_reprocess():
    older = {
        "status": "CLEANUP_ACK",
        "job_id": JOB,
        "algorithmRevision": "3.1-free-r25.9",
    }
    assert not preflight.receipt_matches_revision(older, JOB, CURRENT)
    assert not runtime.receipt_matches_revision(older, JOB, CURRENT)


def test_receipt_writer_and_entrypoint_are_revision_aware():
    master = Path("run_master_3_1_free.py").read_text(encoding="utf-8")
    adapter = Path("run_drive_3_1_free_generic.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
    runtime_source = Path("bridge_runtime_hardening_r25_11.py").read_text(encoding="utf-8")
    assert "'algorithmRevision':ALGORITHM_REVISION" in master
    assert master.rstrip().endswith("return done")
    assert "bridge_runtime_hardening_r25_11" in adapter
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r25.11"' in workflow
    assert "BRIDGE_REQUESTED_WHISPER_MODEL: medium" in workflow
    assert "WHISPER_MODEL: medium" in workflow
    assert "CLEANUP_ACK+KNOWLEDGE_APPLIED" in runtime_source


if __name__ == "__main__":
    test_legacy_receipt_does_not_block_new_revision()
    test_same_revision_receipt_is_idempotent()
    test_knowledge_receipt_is_required_and_revision_aware()
    test_different_revision_can_reprocess()
    test_receipt_writer_and_entrypoint_are_revision_aware()
    print("R25_11_REVISION_IDEMPOTENCY: PASS")
