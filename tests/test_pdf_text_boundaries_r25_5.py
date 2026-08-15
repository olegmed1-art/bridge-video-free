#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import fitz

import bridge_runtime_hardening_r25_5 as r25_5
import bridge_worker_3_1_free as core
import run_master_3_1_free as base


def _master():
    return {
        "source": {
            "name": "known-good-control.avi",
            "durationSeconds": 10,
            "sizeBytes": 123,
            "driveId": "control-drive-id",
            "sha256": "0" * 64,
        },
        "algorithmVersion": "3.1 FREE",
        "algorithmRevision": r25_5.REVISION,
        "session_summary": {"episode_count": 2, "topics": ["торговля"]},
        "technical_qc": {
            "transcript": {
                "primarySource": "local_asr",
                "status": "AUTO-VERIFIED ASR TRANSCRIPT",
                "riskSummary": {},
            }
        },
        "warnings": [],
        "learning_interactions": [
            {
                "focus_episode_id": "e1",
                "task_or_trigger": "контрольный маркер",
                "student_action": "не установлено",
                "intervention_type": "не установлено",
                "teacher_intervention": "",
                "student_response": "не установлена",
                "outcome": "требует проверки",
                "autonomy": "не установлена",
                "transfer": "не проверен",
            }
        ],
        "timeline": [
            {
                "episode_id": "e1",
                "start": 1,
                "end": 2,
                "type": "торговля",
                "topics": ["пас"],
                "confidence": "medium",
            },
            {
                "episode_id": "e1",
                "start": 3,
                "end": 4,
                "type": "розыгрыш",
                "topics": [],
                "confidence": "medium",
            },
        ],
        "student_analysis": {"observations": []},
        "episodes": [],
        "errors": [],
        "strengths": [],
        "teacher_analysis": [],
        "best_explanations": [],
        "canon_links": [],
        "knowledge_gaps": [],
        "recommendations": [],
        "deals": [],
        "decisions": [],
        "transcript": [],
        "content_quality": {},
    }


def main():
    old = os.environ.get("BRIDGE_REQUESTED_ALGORITHM_REVISION")
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = r25_5.REVISION
    try:
        r25_5.install(lambda: "unused-token")
        assert core.ALGORITHM_REVISION == r25_5.REVISION
        assert base.ALGORITHM_REVISION == r25_5.REVISION
        assert r25_5.pdf_text_boundary_issues(
            "confidence: medium00:58:11\nмаркерДействие ученика: нет"
        ) == ["text-boundary-timeline", "text-boundary-learning-cycle"]

        with tempfile.TemporaryDirectory() as td:
            pdf = Path(td) / "control.pdf"
            master = _master()
            base.pdf_report(pdf, master, [])
            base.embed_master(pdf, master)
            qc = base.pdfqc(pdf)
            if not qc.get("ok"):
                raise AssertionError(qc)
            if not (qc.get("textBoundaryCheck") or {}).get("ok"):
                raise AssertionError(qc)
            doc = fitz.open(pdf)
            try:
                text = "\n".join(page.get_text() for page in doc)
            finally:
                doc.close()
            if r25_5.pdf_text_boundary_issues(text):
                raise AssertionError(text)
            if "Алгоритм: 3.1 FREE / 3.1-free-r25.5" not in text:
                raise AssertionError("revision missing from PDF")
            if "контрольный маркерДействие ученика:" in text:
                raise AssertionError("learning-cycle boundary still concatenated")
            if "confidence: medium00:00:03" in text:
                raise AssertionError("timeline boundary still concatenated")
        print("R25_5_PDF_TEXT_BOUNDARY_SELFTEST: PASS")
    finally:
        if old is None:
            os.environ.pop("BRIDGE_REQUESTED_ALGORITHM_REVISION", None)
        else:
            os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = old


if __name__ == "__main__":
    main()
