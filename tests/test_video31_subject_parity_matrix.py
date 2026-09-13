from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/evidence/video31_subject_parity_matrix_2026-08-30.json"


def _load() -> dict:
    return json.loads(MATRIX.read_text(encoding="utf-8"))


def test_subject_parity_matrix_covers_every_required_capability() -> None:
    matrix = _load()
    assert matrix["schema"] == "video31-subject-parity-matrix-v1"
    assert matrix["overall_verdict"] == "FAIL"
    assert matrix["production_activation_allowed"] is False
    assert {item["capability"] for item in matrix["capabilities"]} == {
        "source_identity",
        "offline_asr",
        "asr_qc",
        "diarization",
        "named_speaker",
        "role_attribution",
        "frame_extraction",
        "card_recognition",
        "seat_geometry",
        "board_dealer_vulnerability",
        "auction_extraction",
        "deal_validation",
        "pbn",
        "bridge_semantics",
        "methodology_analysis",
        "learning_episodes",
        "drive_artifacts",
        "terminal_receipt",
        "idempotent_repeat",
    }


def test_no_unit_or_synthetic_evidence_is_misreported_as_parity_proof() -> None:
    matrix = _load()
    allowed = {
        "PARITY_PROVEN",
        "IMPLEMENTED_NOT_PROVEN",
        "PARTIAL",
        "MISSING",
        "NOT_APPLICABLE",
    }
    assert all(item["parity"] in allowed for item in matrix["capabilities"])
    assert all(item["parity"] != "PARITY_PROVEN" for item in matrix["capabilities"])
    assert all(item["blocker"] for item in matrix["capabilities"])


def test_holdout_gate_and_governance_remain_fail_closed() -> None:
    matrix = _load()
    gate = matrix["quality_gate"]
    assert gate == {
        "precision_min": 0.995,
        "recall_min": 0.95,
        "seat_errors_max": 0,
        "measured_precision": None,
        "measured_recall": None,
        "measured_seat_errors": None,
        "status": "INCONCLUSIVE",
    }
    assert matrix["real_video_evidence"]["diana13"] == "INCONCLUSIVE"
    assert matrix["real_video_evidence"]["holdout_metrics_computable"] is False
    assert all(value is False for value in matrix["governance"].values() if value is not True)
    assert matrix["governance"]["unit_tests_are_not_parity_proof"] is True
