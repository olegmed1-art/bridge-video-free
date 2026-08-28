"""School-owned Bridge Vision engine.

This package is the canonical vision boundary for bridge video analysis. Legacy
parsers may be connected only as explicitly named adapters; they are never the
native/default engine.
"""

from .engine import BridgeVisionEngine, VisionCandidate, VisionResult
from .evidence_fusion import CardEvidenceFusionError, fuse_card_evidence

__all__ = [
    "BridgeVisionEngine",
    "CardEvidenceFusionError",
    "VisionCandidate",
    "VisionResult",
    "fuse_card_evidence",
]
