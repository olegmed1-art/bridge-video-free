"""Small dependency-free contracts shared by web and compute runtimes."""

from .video_deal import (
    BRIDGE_VIDEO_DEAL_CONTRACT_VERSION,
    BridgeVideoDealContractError,
    CanonicalHand,
    CanonicalVideoDeal,
    SEATS,
    canonicalize_video_deal,
)

__all__ = [
    "BRIDGE_VIDEO_DEAL_CONTRACT_VERSION",
    "BridgeVideoDealContractError",
    "CanonicalHand",
    "CanonicalVideoDeal",
    "SEATS",
    "canonicalize_video_deal",
]
