"""Bridge School DDS3 public computation boundary."""
from .image_ingress import ImageEnvelope, ImageIngressError, solve_image_envelope
from .model import BridgeDeal, DealValidationError
from .screenshot import ObservedField, ScreenshotDealObservation, derive_board_metadata
from .service import DDS3Config, DDSUnavailable, compute, solve_deal, solve_screenshot_observation, solve_table
__all__=["ImageEnvelope","ImageIngressError","solve_image_envelope","BridgeDeal","DealValidationError","ObservedField","ScreenshotDealObservation","derive_board_metadata","DDS3Config","DDSUnavailable","compute","solve_deal","solve_screenshot_observation","solve_table"]
