#!/usr/bin/env python3
import hashlib
import json
import os
import tempfile
from pathlib import Path

import pymupdf

import bridge_runtime_hardening_r25_6 as r25_6
import bridge_worker_3_1_free as core
import run_master_3_1_free as base


def main():
    os.environ["BRIDGE_REQUESTED_ALGORITHM_REVISION"] = r25_6.REVISION
    r25_6.install(lambda: "test-token")
    assert core.ALGORITHM_REVISION == r25_6.REVISION
    assert base.ALGORITHM_REVISION == r25_6.REVISION

    cycles = [
        {
            "focus_episode_id": f"e{i}",
            "task_or_trigger": f"candidate {i}",
            "student_action": None,
            "teacher_intervention": None,
            "student_response": None,
            "intervention_type": None,
            "outcome": "requires speaker labels",
            "autonomy": "unknown",
            "transfer": "unknown",
        }
        for i in range(40)
    ]
    canon = []
    for i in range(50):
        canon.append(
            {
                "status": "weak",
                "score": round(0.2 + (i % 10) / 100, 2),
                "canonical_excerpt": "Count the hand before choosing the line."
                if i % 2
                else "Plan the play before touching a card.",
            }
        )

    master = {
        "algorithmVersion": "3.1 FREE",
        "algorithmRevision": r25_6.REVISION,
        "source": {
            "name": "synthetic.mp4",
            "durationSeconds": 610,
            "sizeBytes": 1,
            "driveId": "synthetic",
            "sha256": "0" * 64,
        },
        "session_summary": {"episode_count": 0, "topics": []},
        "warnings": [],
        "timeline": [],
        "episodes": [],
        "learning_interactions": cycles,
        "student_analysis": {"observations": []},
        "errors": [],
        "strengths": [],
        "teacher_analysis": [],
        "best_explanations": [],
        "canon_links": canon,
        "knowledge_gaps": [],
        "recommendations": [],
        "deals": [],
        "decisions": [],
        "transcript": [
            {
                "segment_id": "s1",
                "start": 0.0,
                "end": 10.0,
                "text": "bridge lesson",
                "unreliable": False,
            }
        ],
        "technical_qc": {
            "transcript": {
                "primarySource": "local_asr",
                "status": "AUTO-VERIFIED / SEMANTIC-QC PASS",
                "riskSummary": {
                    "maxEstimatedErrorRisk": 0.95,
                    "mediumOrHigherBlocks": 1,
                    "highOrCriticalBlocks": 1,
                },
                "qc": [
                    {"block": 0, "start": 0, "end": 300, "ok": True, "similarity": 0.9},
                    {
                        "block": 1,
                        "start": 300,
                        "end": 600,
                        "ok": False,
                        "similarity": 0.0,
                        "failureReasons": ["empty-control"],
                    },
                ],
            }
        },
        "content_quality": {
            "transcript_segments": 1,
            "semantic_episodes": 0,
            "visual_evidence_items": 0,
            "selected_report_visuals": 0,
            "semantic_qc_status": "PASS",
            "semantic_auto_corrections": 0,
            "semantic_critical_unresolved": 0,
            "actor_attribution_status": "unavailable_without_speaker_labels",
            "actor_specific_claims_excluded": 120,
            "speaker_labels_present": False,
            "unreliable_transcript_segments": 0,
            "unreliable_segments_excluded_from_semantic_derivation": True,
            "semantic_derivation_transcript_segments": 1,
            "deal_candidates": 0,
            "decision_candidates": 0,
            "canon_links_found": 50,
            "r24Gate": {"ok": True, "issues": [], "unreliableDerivedEvidenceCount": 0},
        },
    }

    expected = json.dumps(master, ensure_ascii=False, indent=2).encode()
    expected_sha = hashlib.sha256(expected).hexdigest()

    with tempfile.TemporaryDirectory() as td:
        pdf = Path(td) / "presentation.pdf"
        base.pdf_report(pdf, master, [])
        embedded_sha = base.embed_master(pdf, master)
        assert embedded_sha == expected_sha
        qc = base.pdfqc(pdf)
        assert qc["ok"], qc

        doc = pymupdf.open(pdf)
        text = "\n".join(page.get_text() for page in doc)
        attachment = doc.embfile_get("master_analysis.json")
        pages = doc.page_count
        doc.close()

    assert hashlib.sha256(attachment).hexdigest() == expected_sha
    assert attachment == expected
    assert text.count("Действие ученика: не установлено") == 0
    assert "Надёжных меток говорящих нет." in text
    assert text.count("(score") == 2
    assert "Кандидатов связи с каноном: 50; уникальных формулировок: 2" in text
    assert "Контроль ASR:" in text
    assert "пустых интервалов без первичных речевых сегментов 1" in text
    assert "утечки ненадёжных доказательств в производные выводы — 0" in text
    assert '"transcript_segments"' not in text
    assert pages < 10
    print(
        "R25_6_PDF_PRESENTATION_UNIT: PASS "
        f"pages={pages} attachment_sha256={expected_sha} empty_cycles_collapsed=40 canon_printed=2"
    )


if __name__ == "__main__":
    main()
