from unittest.mock import patch

import pytest

from bridge_contracts.video_deal import BridgeVideoDealContractError
from bridge_vision.deal_review_pdf import DealReviewPdfError, build_deal_review_views


def test_invalid_cards_are_reported_as_input_contract_failure():
    with pytest.raises(DealReviewPdfError, match="canonical 52-card contract"):
        build_deal_review_views({"deals": [{"hands": {"N": ["INVALID"]}}]}, [])


def test_canonicalizer_program_failure_is_not_labeled_invalid_cards():
    with patch("bridge_vision.deal_review_pdf.canonicalize_video_deal", side_effect=RuntimeError("synthetic internal failure")):
        with pytest.raises(RuntimeError, match="synthetic internal failure"):
            build_deal_review_views({"deals": [{"hands": {"N": ["AS"]}}]}, [])


def test_canonicalizer_contract_failure_remains_input_failure():
    with patch("bridge_vision.deal_review_pdf.canonicalize_video_deal", side_effect=BridgeVideoDealContractError("synthetic bad card")):
        with pytest.raises(DealReviewPdfError, match="canonical 52-card contract"):
            build_deal_review_views({"deals": [{"hands": {"N": ["AS"]}}]}, [])
