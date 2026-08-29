from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

import run_master_3_1_free as base
from bridge_contracts.video_deal import SEATS
from bridge_vision.deal_evidence import (
    DealEvidenceError,
    apply_deal_evidence_bundle,
    evidence_payload_sha256,
)
from bridge_vision.deal_pbn import render_deals_pbn


SOURCE = {
    "driveId": "source-drive-id",
    "sha256": "a" * 64,
    "sizeBytes": 123456,
}


def _shot(path: Path, evidence_id: str, timestamp: float, colour: str) -> dict:
    Image.new("RGB", (320, 180), colour).save(path, format="JPEG", quality=90)
    return {
        "evidence_id": evidence_id,
        "time": timestamp,
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source": "video_frame",
    }


def _frame(shot: dict) -> dict:
    return {key: shot[key] for key in ("evidence_id", "time", "sha256")}


def _machine_card(seat: str, card: str, shots: list[dict]) -> dict:
    return {
        "seat": seat,
        "card": card,
        "evidence_class": "OBSERVED_MACHINE",
        "confidence": {"rank": 0.97, "suit": 0.96, "reference": 0.95},
        "channels": {
            "rank": "rank-corner-v1",
            "suit": "suit-symbol-v1",
            "reference": "full-card-reference-v1",
        },
        "frames": [_frame(shot) for shot in shots],
    }


def _bundle(deals: list[dict], *, kind: str = "PROFILED_PIXEL_BACKEND") -> dict:
    producer = {"kind": kind, "revision": "universal-card-observer-v1"}
    if kind in {"PROFILED_PIXEL_BACKEND", "MIXED_REVIEW"}:
        producer.update({
            "backend_sha256": "b" * 64,
            "profile_sha256": "c" * 64,
            "config_sha256": "d" * 64,
        })
    result = {
        "schema": "bridge-3.1-free-deal-evidence/v1",
        "result_scope": "SHADOW_ONLY",
        "canonical_promotion_allowed": False,
        "production_activation_allowed": False,
        "source": dict(SOURCE),
        "producer": producer,
        "deals": deals,
    }
    result["payload_sha256"] = evidence_payload_sha256(result)
    return result


@pytest.mark.parametrize("seat", SEATS)
def test_machine_observation_is_seat_agnostic_and_needs_three_channels(
    tmp_path: Path, seat: str
):
    shots = [
        _shot(tmp_path / f"{seat}-one.jpg", f"{seat}-one", 10.0, "#102030"),
        _shot(tmp_path / f"{seat}-two.jpg", f"{seat}-two", 11.0, "#405060"),
    ]
    cards = {"N": "AS", "E": "KH", "S": "QD", "W": "JC"}
    bundle = _bundle([{
        "deal_id": f"board-{seat}",
        "board_number": 7,
        "card_observations": [_machine_card(seat, cards[seat], shots)],
        "auction": None,
    }])
    result = apply_deal_evidence_bundle(bundle, source=SOURCE, shots=shots)
    assert result["summary"]["observed_card_count"] == 1
    assert result["deals"][0]["hands"][seat] == [cards[seat]]
    assert result["deals"][0]["deal_evidence"]["result_scope"] == "SHADOW_ONLY"
    assert result["canonical_promotion_allowed"] is False


def test_machine_observation_fails_closed_without_two_frames_or_confidence(tmp_path: Path):
    shot = _shot(tmp_path / "one.jpg", "one", 10.0, "#102030")
    observation = _machine_card("N", "AS", [shot])
    observation["confidence"]["suit"] = 0.899
    bundle = _bundle([{
        "deal_id": "board-1",
        "board_number": 1,
        "card_observations": [observation],
        "auction": None,
    }])
    with pytest.raises(DealEvidenceError, match="two independent frames|below 0.90"):
        apply_deal_evidence_bundle(bundle, source=SOURCE, shots=[shot])


@pytest.mark.parametrize("seat", SEATS)
def test_human_verification_is_hash_bound_for_every_seat(tmp_path: Path, seat: str):
    shot = _shot(tmp_path / f"verified-{seat}.jpg", f"verified-{seat}", 20.0, "#708090")
    cards = {"N": "TS", "E": "9H", "S": "8D", "W": "7C"}
    bundle = _bundle([{
        "deal_id": f"verified-{seat}",
        "board_number": 8,
        "card_observations": [{
            "seat": seat,
            "card": cards[seat],
            "evidence_class": "HUMAN_VERIFIED",
            "frames": [_frame(shot)],
        }],
        "verification": {
            "verified_seats": [seat],
            "method": "independent visual review",
            "reviewer": "bridge_expert",
            "verified_at": "2026-08-29T17:00:00Z",
            "reference_frame_sha256": shot["sha256"],
        },
        "auction": None,
    }], kind="HUMAN_REVIEW")
    result = apply_deal_evidence_bundle(bundle, source=SOURCE, shots=[shot])
    deal = result["deals"][0]
    assert deal["verification"]["verified_seats"] == [seat]
    assert deal["evidence"][0] == shot["evidence_id"]


def test_bundle_rejects_wrong_source_and_promotion(tmp_path: Path):
    shots = [
        _shot(tmp_path / "one.jpg", "one", 10.0, "#102030"),
        _shot(tmp_path / "two.jpg", "two", 11.0, "#405060"),
    ]
    deal = {
        "deal_id": "board-1",
        "board_number": 1,
        "card_observations": [_machine_card("N", "AS", shots)],
        "auction": None,
    }
    bundle = _bundle([deal])
    wrong = dict(SOURCE)
    wrong["sha256"] = "f" * 64
    with pytest.raises(DealEvidenceError, match="exact source video"):
        apply_deal_evidence_bundle(bundle, source=wrong, shots=shots)

    bundle = _bundle([deal])
    bundle["canonical_promotion_allowed"] = True
    bundle["payload_sha256"] = evidence_payload_sha256(bundle)
    with pytest.raises(DealEvidenceError, match="canonical promotion"):
        apply_deal_evidence_bundle(bundle, source=SOURCE, shots=shots)


def test_human_card_seat_must_match_reviewed_seats(tmp_path: Path):
    shot = _shot(tmp_path / "verified.jpg", "verified", 20.0, "#708090")
    bundle = _bundle([{
        "deal_id": "mismatched-review",
        "board_number": 8,
        "card_observations": [{
            "seat": "E",
            "card": "AH",
            "evidence_class": "HUMAN_VERIFIED",
            "frames": [_frame(shot)],
        }],
        "verification": {
            "verified_seats": ["N"],
            "method": "independent visual review",
            "reviewer": "bridge_expert",
            "verified_at": "2026-08-29T17:00:00Z",
            "reference_frame_sha256": shot["sha256"],
        },
        "auction": None,
    }], kind="HUMAN_REVIEW")
    with pytest.raises(DealEvidenceError, match="not listed in verified_seats"):
        apply_deal_evidence_bundle(bundle, source=SOURCE, shots=[shot])


def test_complete_auction_and_52_observed_cards_enter_standard_pbn(tmp_path: Path):
    shots = [
        _shot(tmp_path / "one.jpg", "one", 30.0, "#102030"),
        _shot(tmp_path / "two.jpg", "two", 31.0, "#405060"),
    ]
    deck = [rank + suit for suit in "SHDC" for rank in "AKQJT98765432"]
    observations = [
        _machine_card(SEATS[index // 13], card, shots)
        for index, card in enumerate(deck)
    ]
    bundle = _bundle([{
        "deal_id": "complete-board",
        "board_number": 9,
        "card_observations": observations,
        "auction": {
            "status": "COMPLETE_CONFIRMED",
            "dealer": "N",
            "calls": ["1H", "PASS", "2H", "PASS", "PASS", "PASS"],
            "frames": [_frame(shot) for shot in shots],
        },
    }])
    result = apply_deal_evidence_bundle(bundle, source=SOURCE, shots=shots)
    pbn, report = render_deals_pbn(
        result["deals"],
        source_name="universal-lesson.mp4",
        algorithm_revision="3.1-free-r25.17",
    )
    assert '[Deal "N:' in pbn
    assert '[Auction "N"]' in pbn
    assert "1H Pass 2H Pass" in pbn
    assert report["standard_deal_count"] == 1
    assert report["confirmed_auction_count"] == 1
    assert report["canonical_promotion_performed"] is False


def test_39_to_13_is_explicitly_derived_and_not_standard_deal():
    hands = {
        "N": [rank + "S" for rank in "AKQJT98765432"],
        "E": [rank + "H" for rank in "AKQJT98765432"],
        "S": [rank + "D" for rank in "AKQJT98765432"],
        "W": [],
    }
    deal = {
        "deal_id": "derived-board",
        "board_number": 10,
        "hands": hands,
        "auction": None,
        "verification": None,
        "deal_evidence": {
            "result_scope": "SHADOW_ONLY",
            "complete_without_derivation": False,
            "canonical_promotion_allowed": False,
            "production_activation_allowed": False,
        },
    }
    pbn, report = render_deals_pbn(
        [deal], source_name="lesson.mp4", algorithm_revision="3.1-free-r25.17"
    )
    assert '[Deal "' not in pbn
    assert '[X-Derived-W "' in pbn
    assert '[X-Derivation "39_TO_13_DECK_SUBTRACTION"]' in pbn
    assert report["derived_deal_count"] == 1


def test_auction_rejects_insufficient_bid(tmp_path: Path):
    shots = [
        _shot(tmp_path / "one.jpg", "one", 30.0, "#102030"),
        _shot(tmp_path / "two.jpg", "two", 31.0, "#405060"),
    ]
    bundle = _bundle([{
        "deal_id": "bad-auction",
        "board_number": 11,
        "card_observations": [],
        "auction": {
            "status": "PARTIAL_REVIEW",
            "dealer": "N",
            "calls": ["2H", "2D"],
            "frames": [_frame(shot) for shot in shots],
        },
    }])
    with pytest.raises(Exception, match="insufficient bid"):
        apply_deal_evidence_bundle(bundle, source=SOURCE, shots=shots)


def test_runtime_discovers_one_exact_job_bundle_and_preserves_drive_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    shots = [
        _shot(tmp_path / "one.jpg", "one", 40.0, "#102030"),
        _shot(tmp_path / "two.jpg", "two", 41.0, "#405060"),
    ]
    bundle = _bundle([{
        "deal_id": "runtime-board",
        "board_number": 12,
        "card_observations": [_machine_card("E", "AH", shots)],
        "auction": None,
    }])
    source_path = tmp_path / "source-bundle.json"
    source_path.write_text(json.dumps(bundle), encoding="utf-8")
    item = {
        "id": "bundle-drive-id",
        "name": "BRIDGE_DEAL_EVIDENCE_job-1.json",
        "mimeType": "application/json",
        "size": str(source_path.stat().st_size),
    }
    queries: list[str] = []

    def search(_token: str, query: str):
        queries.append(query)
        return [item]

    def download(_token: str, _file_id: str, output: Path):
        output.write_bytes(source_path.read_bytes())

    monkeypatch.setattr(base.io, "search", search)
    monkeypatch.setattr(base.io, "download", download)
    result = base.discover_deal_evidence(
        "token", "parent", "job-1", SOURCE, tmp_path, shots
    )
    assert result is not None
    assert result["deals"][0]["hands"]["E"] == ["AH"]
    assert result["source_file"]["driveId"] == "bundle-drive-id"
    assert queries == [
        "'parent' in parents and trashed=false and name='BRIDGE_DEAL_EVIDENCE_job-1.json'"
    ]


def test_runtime_absent_bundle_is_a_clean_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(base.io, "search", lambda _token, _query: [])
    assert base.discover_deal_evidence(
        "token", "parent", "job-2", SOURCE, tmp_path, []
    ) is None
