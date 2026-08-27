from tools.bridge_video_positions import build_engine


def test_ocr_card_labels_are_explicit_opt_in_only():
    assert build_engine().detector_names == ()
    assert build_engine(allow_ocr_card_labels=True).detector_names == ("ocr-card-labels",)
