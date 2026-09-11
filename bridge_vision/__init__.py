"""School-owned Bridge Vision engine.

This package is the canonical vision boundary for bridge video analysis. Legacy
parsers may be connected only as explicitly named adapters; they are never the
native/default engine.
"""

from .engine import BridgeVisionEngine, VisionCandidate, VisionResult
from .evidence_fusion import CardEvidenceFusionError, fuse_card_evidence
from .bridgit_visible_timeline import VisibleTimelineError, fuse_visible_timeline
from .multiframe import reconstruct_deals, validate_full_deal
from .profiled_challenger import InterfaceProfile, ProfiledCardChallenger, load_profile

__all__ = [
    "BridgeVisionEngine",
    "CardEvidenceFusionError",
    "InterfaceProfile",
    "ProfiledCardChallenger",
    "VisionCandidate",
    "VisionResult",
    "VisibleTimelineError",
    "fuse_card_evidence",
    "fuse_visible_timeline",
    "load_profile",
    "reconstruct_deals",
    "validate_full_deal",
]
