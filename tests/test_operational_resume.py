from __future__ import annotations

from database.run_checkpoint_persistence import VALID_STATES, ingestion_run_id
from database.source_identity_persistence import (
    drive_source_id,
    drive_source_native_key,
)
from database.video_result_persistence import _stable_uuid
from transcript_stage_checkpoint_v1 import _checkpoint_name, _valid_segments


def test_ingestion_run_id_matches_video_persistence_contract():
    job_id = "0123456789abcdef0123456789abcdef"
    assert ingestion_run_id(job_id) == _stable_uuid("ingestion-run", job_id)


def test_checkpoint_state_contract_matches_database_constraint():
    assert VALID_STATES == {"started", "progress", "completed", "failed", "cancelled"}


def test_drive_source_id_matches_video_persistence_contract():
    drive_id = "1AbC_-source"
    assert drive_source_id(drive_id) == _stable_uuid("source", "google-drive", drive_id)
    assert drive_source_native_key(drive_id) == "google-drive:1AbC_-source"


def test_transcript_checkpoint_name_is_revision_scoped():
    name = _checkpoint_name("a" * 32, "3.1-free-r25.15")
    assert name == f"TRANSCRIPT_STAGE_CHECKPOINT_{'a' * 32}_3.1-free-r25.15.json"


def test_transcript_checkpoint_requires_complete_ordered_segments():
    assert _valid_segments([
        {"start": 0.0, "end": 1.2, "text": "Первая реплика"},
        {"start": 1.3, "end": 2.0, "text": "Вторая реплика"},
    ])
    assert not _valid_segments([])
    assert not _valid_segments([
        {"start": 2.0, "end": 1.0, "text": "bad"},
    ])
    assert not _valid_segments([
        {"start": 2.0, "end": 3.0, "text": "later"},
        {"start": 1.0, "end": 1.5, "text": "earlier"},
    ])
    assert not _valid_segments([
        {"start": 0.0, "end": 1.0, "text": ""},
    ])
