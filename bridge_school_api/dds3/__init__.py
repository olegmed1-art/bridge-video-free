"""Bridge School DDS3 public computation boundary."""
from .image_ingress import (
    ImageEnvelope,
    ImageIngressError,
    solve_image_envelope,
    solve_raw_image,
    validate_image_payload,
)
from .model import BridgeDeal, DealValidationError
from .position_runtime import (
    DDS3PositionConfig,
    PositionWorker,
    PositionWorkerUnavailable,
    solve_position_all_moves,
    solve_position_trajectory,
)
from .screenshot import ObservedField, ScreenshotDealObservation, derive_board_metadata
from .service import DDS3Config, DDSUnavailable, compute, solve_deal, solve_screenshot_observation, solve_table
from .vision_local import LocalVisionError, extract_federation_yellow_observation

__all__ = [
    "ImageEnvelope",
    "ImageIngressError",
    "solve_image_envelope",
    "solve_raw_image",
    "validate_image_payload",
    "LocalVisionError",
    "extract_federation_yellow_observation",
    "BridgeDeal",
    "DealValidationError",
    "ObservedField",
    "ScreenshotDealObservation",
    "derive_board_metadata",
    "DDS3PositionConfig",
    "PositionWorker",
    "PositionWorkerUnavailable",
    "solve_position_all_moves",
    "solve_position_trajectory",
    "DDS3Config",
    "DDSUnavailable",
    "compute",
    "solve_deal",
    "solve_screenshot_observation",
    "solve_table",
]
