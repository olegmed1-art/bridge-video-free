from bridge_vision.dependency_audit import audit, load_registry


def test_world_card_runtime_dependencies_are_explicit_and_shadow_bounded():
    registry = load_registry()
    assert registry["cv2"]["kind"] == "infrastructure"
    assert registry["numpy"]["kind"] == "infrastructure"
    assert registry["onnxruntime"]["kind"] == "model"
    assert "SHADOW" in registry["onnxruntime"]["policy"]
    assert "SHA-256" in registry["onnxruntime"]["policy"]

    report = audit()
    assert report["status"] == "PASS"
    assert {"cv2", "numpy", "onnxruntime"} <= set(report["registered"])
