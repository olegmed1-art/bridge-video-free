from pathlib import Path

from bridge_vision import BridgeVisionEngine


def test_native_engine_fuses_non_conflicting_observations_without_inference():
    engine = BridgeVisionEngine(
        {
            "north-detector": lambda _: {"hands": {"N": ["AS", "KH"]}, "confidence": 0.9, "evidence": {"roi": "north"}},
            "south-detector": lambda _: {"hands": {"S": ["QD", "JC"]}, "confidence": 0.8, "evidence": {"roi": "south"}},
        }
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "PARTIAL_BOARD_OBSERVATION"
    assert out["deal"]["hands"]["N"]["cards"] == ["AS", "KH"]
    assert out["deal"]["hands"]["S"]["cards"] == ["QD", "JC"]
    assert out["deal"]["hands"]["E"]["cards"] == []
    assert out["deal"]["hands"]["W"]["cards"] == []
    assert out["deal"]["derivations"] == []


def test_native_engine_fails_closed_on_cross_seat_conflict():
    engine = BridgeVisionEngine(
        {
            "a": lambda _: {"hands": {"N": ["AS"]}, "confidence": 0.9},
            "b": lambda _: {"hands": {"E": ["AS"]}, "confidence": 0.9},
        }
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "CONFLICT"
    assert out["deal"] is None
    assert out["conflicts"][0]["card"] == "AS"


def test_low_confidence_observations_do_not_become_facts():
    engine = BridgeVisionEngine(
        {"weak": lambda _: {"hands": {"N": ["AS", "KS", "QS", "JS"]}, "confidence": 0.2}},
        min_confidence=0.6,
    )
    out = engine.analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "UNAVAILABLE"
    assert out["deal"] is None


def test_no_detector_means_unknown_not_legacy_fallback():
    out = BridgeVisionEngine().analyze_frame(Path("frame.jpg")).to_dict()
    assert out["status"] == "UNAVAILABLE"
    assert out["candidates"] == []
