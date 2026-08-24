"""Unified Research Lab -> Assistant Lab orchestration contract."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .contract import LabContractError, canonical_idempotency_key, validate_job_payload, verify_ben_result, verify_dds3_result

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
    WORLDS = "WORLDS"


_ALLOWED_TRANSITIONS = {
    ResearchStage.QUEUED: {ResearchStage.ACCEPTED, ResearchStage.CANCELLED, ResearchStage.FAILED},
    ResearchStage.ACCEPTED: {ResearchStage.RUNNING, ResearchStage.CANCELLED, ResearchStage.FAILED},
    ResearchStage.RUNNING: {ResearchStage.CHECKPOINTED, ResearchStage.VALIDATING, ResearchStage.FAILED, ResearchStage.CANCELLED},
    ResearchStage.CHECKPOINTED: {ResearchStage.RUNNING, ResearchStage.VALIDATING, ResearchStage.FAILED, ResearchStage.CANCELLED},
    ResearchStage.VALIDATING: {ResearchStage.COMPLETED, ResearchStage.FAILED},
    ResearchStage.COMPLETED: set(), ResearchStage.FAILED: set(), ResearchStage.CANCELLED: set(),
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
    current, nxt = ResearchStage(stage), ResearchStage(target)
    if nxt not in _ALLOWED_TRANSITIONS[current]:
        raise LabContractError(f"invalid ResearchJob transition: {current.value}->{nxt.value}")
    return nxt


def canonical_research_key(kind: ResearchKind | str, payload: Any) -> str:
    normalized_kind = ResearchKind(kind)
    data = _mapping(payload, "payload")
    encoded = json.dumps({"contract": RESEARCH_CONTRACT_VERSION, "kind": normalized_kind.value, "payload": data},
        ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{RESEARCH_CONTRACT_VERSION}:{hashlib.sha256(encoded).hexdigest()}"


def plan_execution(kind: ResearchKind | str, payload: Any) -> ExecutionPlan:
    normalized_kind = ResearchKind(kind)
    data = _mapping(payload, "payload")
    research_key = canonical_research_key(normalized_kind, data)
    if normalized_kind is ResearchKind.DDS3:
        lab_payload = validate_job_payload("DDS3_COMPUTE", data)
        return ExecutionPlan("dds3.compute", "DDS3_COMPUTE", lab_payload,
            canonical_idempotency_key("DDS3_COMPUTE", lab_payload), "assistant_lab_resident_worker_to_oracle_local_dds3")
    if normalized_kind is ResearchKind.BEN:
        lab_payload = validate_job_payload("BEN_COMPUTE", data)
        return ExecutionPlan("ben.compute", "BEN_COMPUTE", lab_payload,
            canonical_idempotency_key("BEN_COMPUTE", lab_payload), "assistant_lab_resident_worker_to_oracle_local_ben")
    if normalized_kind is ResearchKind.WORLDS:
        lab_payload = validate_job_payload("WORLD_GENERATE", data)
        return ExecutionPlan("worlds.generate", "WORLD_GENERATE", lab_payload,
            canonical_idempotency_key("WORLD_GENERATE", lab_payload),
            "assistant_lab_resident_worker_to_oracle_world_generator")
    if normalized_kind is ResearchKind.VIDEO:
        return ExecutionPlan("oracle.audit", None, None, research_key, "universal_video_pipeline")
    return ExecutionPlan("research.composite", None, None, research_key, "research_orchestrator")


def validate_compute_result(kind: ResearchKind | str, result: Any, payload: Any) -> dict[str, Any]:
    normalized_kind = ResearchKind(kind)
    if normalized_kind is ResearchKind.DDS3:
        operation = str(_mapping(payload, "payload").get("operation") or "dd_table")
        return verify_dds3_result(result, expected_operation=operation)
    if normalized_kind is ResearchKind.BEN:
        return verify_ben_result(result)
    if normalized_kind is ResearchKind.WORLDS:
        data = _mapping(result, "world generator result")
        if data.get("engine") != "WORLD_GENERATOR" or data.get("fallback_used") is not False:
            raise LabContractError("world generator provenance mismatch")
        if data.get("complete") is not True or data.get("accepted") != data.get("requested"):
            raise LabContractError("world generator returned an incomplete sample")
        return data
    raise LabContractError("compute result validation is defined only for DDS3/BEN")


def build_artifact_manifest(*, research_id: str, compute_result: Any, provenance: Any, artifact_type: str = "json") -> dict[str, Any]:
    result, prov = _mapping(compute_result, "compute_result"), _mapping(provenance, "provenance")
    if not research_id.strip() or not artifact_type.strip():
        raise LabContractError("research_id and artifact_type are required")
    body = {"contract": RESEARCH_CONTRACT_VERSION, "research_id": research_id, "artifact_type": artifact_type,
        "compute_result": result, "provenance": prov}
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    body["sha256"] = hashlib.sha256(canonical).hexdigest()
    return body


def verify_artifact_manifest(artifact_manifest: Any) -> dict[str, Any]:
    artifact = _mapping(artifact_manifest, "artifact_manifest")
    digest = str(artifact.get("sha256") or "")
    unsigned = dict(artifact); unsigned.pop("sha256", None)
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if digest != hashlib.sha256(canonical).hexdigest():
        raise LabContractError("artifact checksum mismatch")
    return artifact


def build_methodical_input(*, research_id: str, artifact_manifest: Any) -> dict[str, Any]:
    artifact = verify_artifact_manifest(artifact_manifest)
    if artifact.get("research_id") != research_id or artifact.get("contract") != RESEARCH_CONTRACT_VERSION:
        raise LabContractError("artifact/research identity mismatch")
    return {"research_id": research_id, "source_artifact": artifact,
        "instruction": "Transform verified technical evidence into a methodical bridge-school result without changing the underlying evidence.",
        "canonical_promotion": False}


def build_methodical_result(*, research_id: str, artifact_manifest: Any) -> dict[str, Any]:
    artifact = verify_artifact_manifest(artifact_manifest)
    if artifact.get("research_id") != research_id:
        raise LabContractError("artifact/research identity mismatch")
    return {
        "research_id": research_id,
        "status": "READY_FOR_METHODICAL_REVIEW",
        "evidence_sha256": artifact["sha256"],
        "technical_evidence": artifact["compute_result"],
        "provenance": artifact["provenance"],
        "teacher_note": "Technical evidence is verified; pedagogical interpretation must follow the school's approved methodology/materials.",
        "canonical_promotion": False,
    }


__all__ = ["ExecutionPlan","RESEARCH_CONTRACT_VERSION","ResearchJob","ResearchKind","ResearchStage",
    "build_artifact_manifest","verify_artifact_manifest","build_methodical_input","build_methodical_result",
    "canonical_research_key","plan_execution","transition","validate_compute_result"]
