"""School-owned Bridge Vision engine.

This package is the canonical vision boundary for bridge video analysis. Legacy
parsers may be connected only as explicitly named adapters; they are never the
native/default engine.
"""

from .bridgit_autonomous_deals import (
    AutonomousDealsError,
    reconstruct_autonomous_deals,
)
from .bridgit_deal_marker import (
    DealMarkerError,
    assign_stable_deal_markers,
    marker_fingerprint,
)
from .bridgit_gambler_recovery_pipeline import (
    GamblerRecoveryPipelineError,
    recover_with_original_gambler_deck,
)
from .bridgit_played_card_observer import build_suit_bank, observe_played_cards
from .bridgit_unresolved_card_recovery import (
    UnresolvedRecoveryError,
    recover_unresolved_deal,
    scan_unresolved_in_registered_frame,
    unresolved_cards,
)
from .bridgit_visible_hand_observer import (
    ObserverProfile,
    VisibleHandObserverError,
)
from .bridgit_visible_hand_observer import (
    observe_frame as observe_visible_hands,
)
from .bridgit_visible_hand_observer import (
    parse_profile as parse_visible_hand_profile,
)
from .bridgit_visible_timeline import VisibleTimelineError, fuse_visible_timeline
from .engine import BridgeVisionEngine, VisionCandidate, VisionResult
from .evidence_fusion import CardEvidenceFusionError, fuse_card_evidence
from .multiframe import reconstruct_deals, validate_full_deal
from .profiled_challenger import InterfaceProfile, ProfiledCardChallenger, load_profile

__all__ = [
    "AutonomousDealsError",
    "BridgeVisionEngine",
    "CardEvidenceFusionError",
    "DealMarkerError",
    "GamblerRecoveryPipelineError",
    "InterfaceProfile",
    "ObserverProfile",
    "ProfiledCardChallenger",
    "UnresolvedRecoveryError",
    "VisibleHandObserverError",
    "VisibleTimelineError",
    "VisionCandidate",
    "VisionResult",
    "assign_stable_deal_markers",
    "build_suit_bank",
    "fuse_card_evidence",
    "fuse_visible_timeline",
    "load_profile",
    "marker_fingerprint",
    "observe_played_cards",
    "observe_visible_hands",
    "parse_visible_hand_profile",
    "recover_unresolved_deal",
    "recover_with_original_gambler_deck",
    "reconstruct_autonomous_deals",
    "reconstruct_deals",
    "scan_unresolved_in_registered_frame",
    "unresolved_cards",
    "validate_full_deal",
]
