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
from .legacy_report_adapter import (
    ANNOTATION_QUEUE_SCHEMA,
    LEGACY_REPORT_SCHEMA,
    LegacyReportAdapterError,
    adapt_legacy_report_pointers,
)
from .longitudinal_pilot import (
    LONGITUDINAL_PILOT_SCHEMA,
    run_multi_lesson_pilot,
)
from .methodology_queue import (
    CANDIDATE_DECISION_SCHEMA,
    CANDIDATE_REVIEW_REQUEST_SCHEMA,
    DECISION_SCHEMA,
    QUEUE_SCHEMA,
    MethodologyQueueError,
    build_methodology_review_queue,
    build_candidate_review_request,
    record_candidate_review_decision,
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
from .learning_module import MODULE_SCHEMA, LearningModuleError, validate_learning_module
from .adaptive_selector import (
    SELECTOR_POLICY_SCHEMA,
    SELECTOR_SCHEMA,
    AdaptiveSelectorError,
    select_next_activity,
)
from .artifact_readiness import (
    ARTIFACT_READINESS_SCHEMA,
    ArtifactReadinessError,
    assess_artifact_readiness,
)

__all__ = [
    "ADAPTER_SCHEMA",
    "ANNOTATION_QUEUE_SCHEMA",
    "CATALOG_ADAPTER_SCHEMA",
    "AUTHORITY_CLASS",
    "CATALOG_SCHEMA",
    "CATALOG_VERSION",
    "COURSE_VERSION",
    "CANDIDATE_DECISION_SCHEMA",
    "CANDIDATE_REVIEW_REQUEST_SCHEMA",
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
    "LEGACY_REPORT_SCHEMA",
    "LONGITUDINAL_PILOT_SCHEMA",
    "LegacyReportAdapterError",
    "MasteryEvidenceError",
    "MethodologyQueueError",
    "QUEUE_SCHEMA",
    "MODULE_SCHEMA",
    "LearningModuleError",
    "SELECTOR_POLICY_SCHEMA",
    "SELECTOR_SCHEMA",
    "AdaptiveSelectorError",
    "ARTIFACT_READINESS_SCHEMA",
    "ArtifactReadinessError",
    "adapt_legacy_report_pointers",
    "adapt_video31_quality",
    "adapt_video31_quality_with_catalog",
    "build_methodology_review_queue",
    "build_candidate_review_request",
    "build_skill_trajectory",
    "canonical_sha256",
    "record_methodology_decision",
    "record_candidate_review_decision",
    "resolve_reviewed_skill",
    "validate_catalog",
    "validate_episode",
    "run_longitudinal_pilot",
    "run_multi_lesson_pilot",
    "eligible_next_skills",
    "evaluate_mastery_evidence",
    "validate_mastery_policy",
    "validate_learning_module",
    "select_next_activity",
    "assess_artifact_readiness",
]
