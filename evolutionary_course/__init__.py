"""Evolutionary Course v1 research-candidate package."""

from .contract import (
    AUTHORITY_CLASS,
    COURSE_VERSION,
    EPISTEMIC_CLASSES,
    EpisodeContractError,
    SCHEMA,
    SKILL_STATES,
    build_skill_trajectory,
    canonical_sha256,
    validate_episode,
)
from .video31_adapter import (
    ADAPTER_SCHEMA,
    Video31AdapterError,
    adapt_video31_quality,
)

__all__ = [
    "ADAPTER_SCHEMA",
    "AUTHORITY_CLASS",
    "COURSE_VERSION",
    "EPISTEMIC_CLASSES",
    "EpisodeContractError",
    "SCHEMA",
    "SKILL_STATES",
    "Video31AdapterError",
    "adapt_video31_quality",
    "build_skill_trajectory",
    "canonical_sha256",
    "validate_episode",
]
