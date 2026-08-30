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
from .skill_catalog import (
    CATALOG_SCHEMA,
    CATALOG_VERSION,
    SkillCatalogError,
    resolve_reviewed_skill,
    validate_catalog,
)
from .methodology_queue import (
    DECISION_SCHEMA,
    QUEUE_SCHEMA,
    MethodologyQueueError,
    build_methodology_review_queue,
    record_methodology_decision,
)
from .video31_adapter import (
    ADAPTER_SCHEMA,
    CATALOG_ADAPTER_SCHEMA,
    Video31AdapterError,
    adapt_video31_quality,
    adapt_video31_quality_with_catalog,
)
from .pilot import PILOT_SCHEMA, run_longitudinal_pilot
from .mastery import (
    MASTERY_POLICY_SCHEMA,
    MASTERY_REPORT_SCHEMA,
    MasteryEvidenceError,
    eligible_next_skills,
    evaluate_mastery_evidence,
    validate_mastery_policy,
)

__all__ = [
    "ADAPTER_SCHEMA",
    "CATALOG_ADAPTER_SCHEMA",
    "AUTHORITY_CLASS",
    "CATALOG_SCHEMA",
    "CATALOG_VERSION",
    "COURSE_VERSION",
    "DECISION_SCHEMA",
    "EPISTEMIC_CLASSES",
    "EpisodeContractError",
    "SCHEMA",
    "SkillCatalogError",
    "SKILL_STATES",
    "Video31AdapterError",
    "PILOT_SCHEMA",
    "MASTERY_POLICY_SCHEMA",
    "MASTERY_REPORT_SCHEMA",
    "MasteryEvidenceError",
    "MethodologyQueueError",
    "QUEUE_SCHEMA",
    "adapt_video31_quality",
    "adapt_video31_quality_with_catalog",
    "build_methodology_review_queue",
    "build_skill_trajectory",
    "canonical_sha256",
    "record_methodology_decision",
    "resolve_reviewed_skill",
    "validate_catalog",
    "validate_episode",
    "run_longitudinal_pilot",
    "eligible_next_skills",
    "evaluate_mastery_evidence",
    "validate_mastery_policy",
]
