"""Small dependency-free contracts shared by web and compute runtimes."""

from .video_deal import (
    BRIDGE_VIDEO_DEAL_CONTRACT_VERSION,
    BridgeVideoDealContractError,
    CanonicalHand,
    CanonicalVideoDeal,
    SEATS,
    canonicalize_video_deal,
)
from .video_frame import (
    BRIDGE_VIDEO_FRAME_CONTRACT_VERSION,
    BridgeVideoFrameContractError,
    CanonicalVideoFrame,
    PARSER_STATUSES,
    canonicalize_frame_recognition,
)

__all__ = [
    "BRIDGE_VIDEO_DEAL_CONTRACT_VERSION",
    "BRIDGE_VIDEO_FRAME_CONTRACT_VERSION",
    "BridgeVideoDealContractError",
    "BridgeVideoFrameContractError",
    "CanonicalHand",
    "CanonicalVideoDeal",
    "CanonicalVideoFrame",
    "PARSER_STATUSES",
    "SEATS",
    "canonicalize_frame_recognition",
    "canonicalize_video_deal",
]
