from pathlib import Path

import pytest

from bridge_vision.world_card_backends import (
    ProfiledWorldReferenceComposer,
    RoboflowCardDetector,
    WorldCardBackendError,
    WorldCardPrediction,
    normalize_card_class,
)


def prediction(card="AS", *, x=10, y=20, confidence=0.97, model_id="lgd-cards-gen3:onnx"):
    return WorldCardPrediction(
        card=card,
        confidence=confidence,
        box={"x": x, "y": y, "w": 20, "h": 30},
        model_id=model_id,
    )


def glyph_payload(frame, profile):
    return {
        "frame_sha256": "a" * 64,
        "registration": {},
        "deal_identity": {"kind": "EXPLICIT_BOARD", "scope": "test", "value": "board-1"},
        "cards": [{
            "box": {"x": 10, "y": 20, "w": 20, "h": 30},
            "rank": {"value": "A", "confidence": 0.98, "channel_id": "glyph-rank-suit-v1"},
            "suit": {"value": "S", "confidence": 0.96, "channel_id": "glyph-rank-suit-v1"},
        }],
    }


def test_world_card_class_normalization_is_bounded():
    assert normalize_card_class("10-hearts") == "TH"
    assert normalize_card_class("spades-A") == "AS"
    with pytest.raises(WorldCardBackendError, match="invalid card class"):
        normalize_card_class("joker")


def test_composer_keeps_rank_suit_separate_and_adds_world_reference(tmp_path: Path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    composer = ProfiledWorldReferenceComposer(glyph_payload, lambda _: [prediction()])

    result = composer(frame, {"reference_channel_id": "full-card-reference-v1"})

    card = result["cards"][0]
    assert card["rank"]["value"] == "A"
    assert card["suit"]["value"] == "S"
    assert card["reference_match"] == {
        "card": "AS",
        "confidence": 0.97,
        "channel_id": "full-card-reference-v1",
        "model_id": "lgd-cards-gen3:onnx",
    }
    assert result["world_reference"]["matched_count"] == 1
    assert result["world_reference"]["external_frame_transfer"] is False


def test_composer_fails_closed_on_missing_or_ambiguous_geometry(tmp_path: Path):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"frame")
    missing = ProfiledWorldReferenceComposer(glyph_payload, lambda _: [prediction(x=200, y=200)])
    assert missing(frame, {})["cards"] == []

    ambiguous = ProfiledWorldReferenceComposer(
        glyph_payload,
        lambda _: [prediction("AS"), prediction("KH")],
    )
    assert ambiguous(frame, {})["cards"] == []


def test_roboflow_adapter_is_explicit_and_marks_external_transfer(tmp_path: Path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"predictions": [{
                "class": "Q-diamonds",
                "confidence": 0.94,
                "x": 100,
                "y": 80,
                "width": 20,
                "height": 30,
            }]}

    def post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr("bridge_vision.world_card_backends.requests.post", post)
    detector = RoboflowCardDetector(model="playing-cards-pzvb1", version=1, api_key="secret")
    rows = detector(frame)

    assert captured["url"].endswith("/playing-cards-pzvb1/1")
    assert captured["params"]["api_key"] == "secret"
    assert rows[0].card == "QD"
    assert rows[0].box == {"x": 90.0, "y": 65.0, "w": 20.0, "h": 30.0}
    composer = ProfiledWorldReferenceComposer(glyph_payload, lambda _: rows, min_iou=0.01)
    result = composer(frame, {})
    assert result["world_reference"]["external_frame_transfer"] is True


def test_roboflow_adapter_rejects_unbounded_response(tmp_path: Path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    frame.write_bytes(b"jpeg")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"predictions": [{}] * 105}

    monkeypatch.setattr("bridge_vision.world_card_backends.requests.post", lambda *a, **k: Response())
    detector = RoboflowCardDetector(model="playing-cards-pzvb1", version=1, api_key="secret")
    with pytest.raises(WorldCardBackendError, match="invalid Roboflow response"):
        detector(frame)
