#!/usr/bin/env python3
"""Rollback-only production persistence smoke test for bridge_school_worker_principal."""
from __future__ import annotations

import os

from database.video_result_persistence import persist_video_result


def main() -> None:
    master = {
        "schema": "bridge-video-master-analysis",
        "schemaVersion": 2,
        "algorithmVersion": "3.1 FREE",
        "algorithmRevision": "runtime-persistence-smoke",
        "job_id": "runtime-persistence-smoke",
        "source": {
            "driveId": "runtime-smoke-source",
            "name": "runtime-smoke.mp4",
            "mimeType": "video/mp4",
            "sizeBytes": 1024,
            "durationSeconds": 15.0,
            "sha256": "1" * 64,
            "parentFolderId": "runtime-smoke-parent",
        },
        "transcript": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "Проверка записи транскрипта.",
                "speaker": None,
                "source": "runtime_smoke",
                "unreliable": False,
            },
            {
                "start": 3.0,
                "end": 6.0,
                "text": "Проверка записи анализа.",
                "speaker": None,
                "source": "runtime_smoke",
                "unreliable": False,
            },
        ],
        "episodes": [{"episode_id": "runtime-smoke-episode"}],
        "technical_qc": {
            "transcript": {"primarySource": "local_asr", "language": "ru"},
            "visual": {"pass2": {"status": "VISUAL_PASS_2_COMPLETE"}},
        },
        "content_quality": {
            "semantic_auto_corrections": 0,
            "semantic_qc_status": "PASS",
        },
    }
    done = {
        "status": "AI_DONE",
        "job_id": "runtime-persistence-smoke",
        "masterPdf": {
            "driveId": "runtime-smoke-report",
            "name": "runtime-smoke.pdf",
            "sizeBytes": 2048,
            "sha256": "2" * 64,
            "masterJsonEmbedded": True,
            "masterJsonSha256": "3" * 64,
        },
    }
    result = persist_video_result(
        os.environ.get("BRIDGE_WORKER_DATABASE_URL", ""),
        master,
        done,
        rollback=True,
    )
    if not result.get("rolled_back") or result.get("segments") != 2:
        raise SystemExit("RUNTIME_DB_PERSISTENCE_SMOKE: FAIL")
    print("RUNTIME_DB_PERSISTENCE_SMOKE: PASS rollback=true path=full-result-graph")


if __name__ == "__main__":
    main()
