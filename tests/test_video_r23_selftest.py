#!/usr/bin/env python3
"""Synthetic r23 regression tests. No user media, Drive access, or secrets."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types

# The semantic adapter imports DB persistence. Stub it before import so these
# synthetic tests require no PostgreSQL credential or live external service.
stub = types.ModuleType("bridge_neon_persistence")
stub.persist_completed_drive_job = lambda token: None
sys.modules.setdefault("bridge_neon_persistence", stub)

import run_master_3_1_free_semantic as r23  # noqa: E402


def _segments(blocks: int = 23):
    return [
        {
            "start": i * 300.0 + 1.0,
            "end": i * 300.0 + 3.0,
            "text": f"контракт пика блок {i}",
            "speaker": None,
            "unreliable": False,
        }
        for i in range(blocks)
    ]


def _patch_audio(monkey_bad_block: int | None = None):
    old_wav = r23.base.io.wav
    old_asr = r23.base.asr_text
    old_safe = r23.base.io.safe

    def fake_wav(video, out, start, duration):
        return None

    def fake_asr(path, strict=False, qc_retry=False):
        name = Path(path).name
        # q004.wav and q004-fresh.wav both identify block 4.
        block = int(name[1:4])
        if monkey_bad_block is not None and block == monkey_bad_block:
            return "совсем другой разговор без нужных терминов"
        return f"контракт пика блок {block}"

    r23.base.io.wav = fake_wav
    r23.base.asr_text = fake_asr
    r23.base.io.safe = lambda **kwargs: None
    return old_wav, old_asr, old_safe


def _restore_audio(old):
    r23.base.io.wav, r23.base.asr_text, r23.base.io.safe = old


def test_all_23_windows_receive_independent_qc():
    segs = _segments(23)
    old = _patch_audio()
    try:
        qc, passed = r23.qc_transcript_r6("synthetic.mp4", Path("/tmp"), 23 * 300.0, segs)
    finally:
        _restore_audio(old)
    assert passed
    assert len(qc) == 23, len(qc)
    assert [x["block"] for x in qc] == list(range(23))
    assert all(x["ok"] for x in qc)
    assert all(x["riskCalibrated"] is False for x in qc)


def test_failed_bridge_block_keeps_evidence_and_becomes_unreliable():
    segs = _segments(23)
    old = _patch_audio(monkey_bad_block=4)
    try:
        qc, passed = r23.qc_transcript_r6("synthetic.mp4", Path("/tmp"), 23 * 300.0, segs)
    finally:
        _restore_audio(old)
    assert passed  # one isolated bad window is allowed only as an explicit warning.
    bad = qc[4]
    assert bad["ok"] is False
    assert bad["riskBand"] == "CRITICAL"
    assert bad["estimatedErrorRisk"] >= 0.80
    assert bad["attempts"] == 3
    assert len(bad["qcEvidence"]) == 3
    assert "BRIDGE_TERM_MISMATCH" in bad["failureReasons"]
    assert any(s["unreliable"] for s in segs if 1200 <= s["start"] < 1500)


def test_same_revision_done_is_detected_before_heavy_processing():
    old_search = r23.base.io.search
    old_read = r23.base._read_text
    try:
        r23.base.io.search = lambda token, query: ([{"id": "ready1", "modifiedTime": "2026-08-13T19:01:00Z"}]
            if "METHODOLOGY_READY" in query else [{"id": "done1", "modifiedTime": "2026-08-13T19:00:00Z"}])
        payload = {
            "status": "AI_DONE",
            "job_id": "a" * 32,
            "algorithmRevision": r23.core.ALGORITHM_REVISION,
            "masterPdf": {"driveId": "pdf1"},
        }
        ready = {"status":"METHODOLOGY_READY","job_id":"a"*32,
                 "algorithmRevision":r23.core.ALGORITHM_REVISION,"masterPdfDriveId":"pdf1"}
        r23.base._read_text = lambda token, item: json.dumps(ready if item["id"]=="ready1" else payload, ensure_ascii=False)
        found = r23._existing_same_revision_done("token", "a" * 32)
    finally:
        r23.base.io.search = old_search
        r23.base._read_text = old_read
    assert found == payload


def test_ai_done_without_methodology_ready_does_not_skip_processing():
    old_search = r23.base.io.search
    old_read = r23.base._read_text
    try:
        r23.base.io.search = lambda token, query: [] if "METHODOLOGY_READY" in query else [{"id":"done1"}]
        payload={"status":"AI_DONE","job_id":"c"*32,"algorithmRevision":r23.core.ALGORITHM_REVISION,
                 "masterPdf":{"driveId":"pdf1"}}
        r23.base._read_text=lambda token,item:json.dumps(payload)
        assert r23._existing_same_revision_done("token","c"*32) is None
    finally:
        r23.base.io.search=old_search
        r23.base._read_text=old_read


def test_already_done_reconciles_database_without_heavy_reprocessing():
    job_id = "b" * 32
    payload = {
        "status": "AI_DONE",
        "job_id": job_id,
        "algorithmRevision": r23.core.ALGORITHM_REVISION,
        "masterPdf": {"driveId": "pdf1"},
    }
    old_job = os.environ.get("BRIDGE_JOB_ID")
    old_existing = r23._existing_same_revision_done
    old_process = r23.base.process_job
    old_persist = r23.persist_completed_drive_job
    old_safe = r23.base.io.safe
    persistence_calls = []
    try:
        os.environ["BRIDGE_JOB_ID"] = job_id
        r23._existing_same_revision_done = lambda token, requested_job_id: payload

        def heavy_processing_must_not_run(token):
            raise AssertionError("heavy processing must not rerun for existing AI_DONE")

        r23.base.process_job = heavy_processing_must_not_run
        r23.persist_completed_drive_job = lambda token: persistence_calls.append(token)
        r23.base.io.safe = lambda **kwargs: None
        result = r23.process_job("synthetic-token")
    finally:
        if old_job is None:
            os.environ.pop("BRIDGE_JOB_ID", None)
        else:
            os.environ["BRIDGE_JOB_ID"] = old_job
        r23._existing_same_revision_done = old_existing
        r23.base.process_job = old_process
        r23.persist_completed_drive_job = old_persist
        r23.base.io.safe = old_safe

    assert result == payload
    assert persistence_calls == ["synthetic-token"]


def test_workflow_is_one_shot_and_serialized():
    text = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(encoding="utf-8")
    assert "group: bridge-video-heavy" in text
    assert "PUSH_JOB_ID" not in text
    assert "README.md" not in text
    assert "run_requests/*.txt" in text


def main():
    tests = [
        test_all_23_windows_receive_independent_qc,
        test_failed_bridge_block_keeps_evidence_and_becomes_unreliable,
        test_same_revision_done_is_detected_before_heavy_processing,
        test_ai_done_without_methodology_ready_does_not_skip_processing,
        test_already_done_reconciles_database_without_heavy_reprocessing,
        test_workflow_is_one_shot_and_serialized,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"PASS r23 synthetic selftests: {len(tests)}")


if __name__ == "__main__":
    main()
