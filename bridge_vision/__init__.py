"""School-owned Bridge Vision engine.

This package is the canonical vision boundary for bridge video analysis. Legacy
parsers may be connected only as explicitly named adapters; they are never the
native/default engine.
"""

from .engine import BridgeVisionEngine, VisionCandidate, VisionResult

__all__ = ["BridgeVisionEngine", "VisionCandidate", "VisionResult"]
