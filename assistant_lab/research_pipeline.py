"""Unified Research Lab -> Assistant Lab orchestration contract.

This layer is deliberately pure and fail-closed. It defines the durable envelope that
connects chat/research requests to bounded compute, database evidence, artifacts and
methodical transformation without letting any layer silently impersonate another.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .contract import LabContractError, canonical_idempotency_key, validate_job_payload

RESEARCH_CONTRACT_VERSION = "bridge-research-job-v1"


class ResearchStage(str, Enum):
    QUEUED = "QUEUED"
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    CHECKPOINTED = "CHECKPOINTED"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ResearchKind(str, Enum):
    DDS3 = "DDS3"
    BEN = "BEN"
    VIDEO = "VIDEO"
    COMPOSITE = "COMPOSITE"


_ALLOWED_TRANSITIONS = {
    ResearchStage.QUEUED: {ResearchStage.ACCEPTED, ResearchStage.CANCELLED, ResearchStage.FAILED},
    ResearchStage.ACCEPTED: {ResearchStage.RUNNING, ResearchStage.CANCELLED, ResearchStage.FAILED},
    ResearchStage.RUNNING: {ResearchStage.CHECKPOINTED, ResearchStage.VALIDATING, ResearchStage.FAILED, ResearchStage.CANCELLED},
    ResearchStage.CHECKPOINTED: {ResearchStage.RUNNING, ResearchStage.VALIDATING, ResearchStage.FAILED, ResearchStage.CANCELLED},
    ResearchStage.VALIDATING: {ResearchStage.COMPLETED, ResearchStage.FAILED},
    ResearchStage.COMPLETED: set(),
    ResearchStage.FAILED: set(),
    ResearchStage.CANCELLED: set(),
}


@dataclass(frozen=True)
class ResearchJob:
    research_id: str
    kind: ResearchKind
    payload: dict[str, Any]
    stage: ResearchStage = ResearchStage.QUEUED
    source: str = "CHAT"


@dataclass(frozen=True)
class ExecutionPlan:
    capability: str
    assistant_lab_kind: str | None
    assistant_lab_payload: dict[str, Any] | None
    idempotency_key: str
    execution_boundary: str


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise LabContractError(f"{field} must be an object")
    return dict(value)


def transition(stage: ResearchStage | str, target: ResearchStage | str) -> ResearchStage:
    current = ResearchStage(stage)
    nxt = ResearchStage(target)
    if nxt not in _ALLOWED_TRANSITIONS[current]:
        raise LabContractError(f"invalid ResearchJob transition: {current.value}->{nxt.value}")
    return nxt


def canonical_research_key(kind: ResearchKind | str, payload: Any) -> str:
    normalized_kind = ResearchKind(kind)
    data = _mapping(payload, "payload")
    encoded = json.dumps(
        {"contract": RESEARCH_CONTRACT_VERSION, "kind": normalized_kind.value, "payload": data},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{RESEARCH_CONTRACT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def plan_execution(kind: ResearchKind | str, payload: Any) -> ExecutionPlan:
    normalized_kind = ResearchKind(kind)
    data = _mapping(payload, "payload")
    research_key = canonical_research_key(normalized_kind, data)

    if normalized_kind is ResearchKind.DDS3:
        lab_payload = validate_job_payload("DDS3_COMPUTE", data)
        return ExecutionPlan(
            capability="dds3.compute",
            assistant_lab_kind="DDS3_COMPUTE",
            assistant_lab_payload=lab_payload,
            idempotency_key=canonical_idempotency_key("DDS3_COMPUTE", lab_payload),
            execution_boundary="assistant_lab_resident_worker_to_oracle_local_dds3",
        )

    if normalized_kind is ResearchKind.BEN:
        # BEN is registered as an approved resident-worker capability, but the v1 queue
        # does not yet have a BEN_COMPUTE job kind. Keep the common ResearchJob envelope
        # while refusing to fabricate an executable child job.
        return ExecutionPlan(
            capability="ben.compute",
            assistant_lab_kind=None,
            assistant_lab_payload=None,
            idempotency_key=research_key,
            execution_boundary="resident_worker_required_ben_adapter",
        )

    if normalized_kind is ResearchKind.VIDEO:
        return ExecutionPlan(
            capability="oracle.audit",
            assistant_lab_kind=None,
            assistant_lab_payload=None,
            idempotency_key=research_key,
            execution_boundary="universal_video_pipeline",
        )

    return ExecutionPlan(
        capability="research.composite",
        assistant_lab_kind=None,
        assistant_lab_payload=None,
        idempotency_key=research_key,
        execution_boundary="research_orchestrator",
    )


def build_artifact_manifest(
    *, research_id: str, compute_result: Any, provenance: Any, artifact_type: str = "json"
) -> dict[str, Any]:
    result = _mapping(compute_result, "compute_result")
    prov = _mapping(provenance, "provenance")
    if not research_id.strip():
        raise LabContractError("research_id is required")
    if not artifact_type.strip():
        raise LabContractError("artifact_type is required")
    return {
        "contract": RESEARCH_CONTRACT_VERSION,
        "research_id": research_id,
        "artifact_type": artifact_type,
        "compute_result": result,
        "provenance": prov,
    }


def build_methodical_input(*, research_id: str, artifact_manifest: Any) -> dict[str, Any]:
    artifact = _mapping(artifact_manifest, "artifact_manifest")
    if artifact.get("research_id") != research_id:
        raise LabContractError("artifact/research identity mismatch")
    if artifact.get("contract") != RESEARCH_CONTRACT_VERSION:
        raise LabContractError("artifact contract mismatch")
    return {
        "research_id": research_id,
        "source_artifact": artifact,
        "instruction": "Transform verified technical evidence into a methodical bridge-school result without changing the underlying evidence.",
        "canonical_promotion": False,
    }


__all__ = [
    "ExecutionPlan",
    "RESEARCH_CONTRACT_VERSION",
    "ResearchJob",
    "ResearchKind",
    "ResearchStage",
    "build_artifact_manifest",
    "build_methodical_input",
    "canonical_research_key",
    "plan_execution",
    "transition",
]
