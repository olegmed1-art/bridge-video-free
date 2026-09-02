"""Universal educational video analyzer core.

Domain-neutral media/transcription infrastructure. Bridge-specific interpretation
lives behind profiles/plugins and must not leak into the core contract.
"""

from .contract import CONTRACT_VERSION, VideoJob, VideoContractError, validate_job
from .profiles import PROFILES, resolve_profile

__all__ = [
    "CONTRACT_VERSION",
    "VideoJob",
    "VideoContractError",
    "PROFILES",
    "resolve_profile",
    "validate_job",
]
