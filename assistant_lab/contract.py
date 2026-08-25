"""Compatibility wrapper for the shared Assistant Lab contract.

The canonical bounded contract lives in ``bridge_contracts.assistant_lab`` so
Vercel can use the same validation/provenance rules without bundling the
Assistant Lab compute package.
"""
from bridge_contracts.assistant_lab import (
    ALLOWED_DDS3_OPERATIONS,
    ALLOWED_KINDS,
    CONTRACT_VERSION,
    LabContractError,
    LabJob,
    Priority,
    canonical_idempotency_key,
    validate_job_payload,
    validate_priority,
    verify_ben_result,
    verify_dds3_result,
)

__all__ = [
    "ALLOWED_DDS3_OPERATIONS",
    "ALLOWED_KINDS",
    "CONTRACT_VERSION",
    "LabContractError",
    "LabJob",
    "Priority",
    "canonical_idempotency_key",
    "validate_job_payload",
    "validate_priority",
    "verify_ben_result",
    "verify_dds3_result",
]
