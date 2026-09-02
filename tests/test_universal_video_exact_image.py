from __future__ import annotations

import re
from pathlib import Path

from universal_video.import_preflight import MODULES

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "deploy" / "oracle-universal-video" / "Dockerfile"
WORKFLOW = ROOT / ".github" / "workflows" / "issue-881-canary-readiness.yml"


def test_image_has_immutable_base_and_source_label():
    text = DOCKERFILE.read_text(encoding="utf-8")
    first_from = next(line for line in text.splitlines() if line.startswith("FROM "))
    assert re.fullmatch(r"FROM [^ ]+@sha256:[0-9a-f]{64}", first_from)
    assert ":latest" not in text.lower()
    assert 'org.opencontainers.image.revision="${UNIVERSAL_VIDEO_SOURCE_COMMIT}"' in text
    assert "UNIVERSAL_VIDEO_SOURCE_COMMIT=${UNIVERSAL_VIDEO_SOURCE_COMMIT}" in text


def test_image_copies_required_runtime_closure():
    text = DOCKERFILE.read_text(encoding="utf-8")
    for required in (
        "COPY universal_video ./universal_video",
        "bridge_worker_3_1_free.py",
        "bridge_runtime_hardening_r25_16.py",
        "route_drive_job_outputs.py",
        "COPY bridge_vision ./bridge_vision",
        "COPY bridge_contracts ./bridge_contracts",
        "COPY database ./database",
        "COPY tools ./tools",
    ):
        assert required in text
    assert {
        "universal_video.neon_worker",
        "bridge_worker_3_1_free",
        "bridge_runtime_hardening_r25_16",
        "route_drive_job_outputs",
        "bridge_vision",
        "bridge_contracts",
        "database.runtime_worker_preflight",
        "psycopg",
    } <= set(MODULES)


def test_readiness_workflow_cannot_enqueue_or_process_media():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert not re.search(r"single_canary\s+enqueue", text)
    assert not re.search(r"python\s+-m\s+universal_video\.exact_canary_worker", text)
    assert "video_job_submitted':False" in text
    assert "real_media_read':False" in text
    assert "real_video_result_written':False" in text
    assert "canonical_promotion_allowed':False" in text
    assert "publication_state':'NOT_PUBLISHED'" in text
