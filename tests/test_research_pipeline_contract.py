import pytest

from assistant_lab.contract import LabContractError
from assistant_lab.research_pipeline import (
    RESEARCH_CONTRACT_VERSION,
    ResearchKind,
    ResearchStage,
    build_artifact_manifest,
    build_methodical_input,
    canonical_research_key,
    plan_execution,
    transition,
)


def test_dds3_research_job_maps_to_existing_resident_worker_contract():
    payload = {"operation": "dd_table", "pbn": "N:AKQJ.T98.765.432 ..."}
    plan = plan_execution(ResearchKind.DDS3, payload)
    assert plan.capability == "dds3.compute"
    assert plan.assistant_lab_kind == "DDS3_COMPUTE"
    assert plan.assistant_lab_payload == payload
    assert plan.idempotency_key.startswith("assistant-lab-v1:")
    assert "oracle_local_dds3" in plan.execution_boundary


def test_ben_maps_to_bounded_resident_worker_contract():
    payload = {"hand": "AKQJ.T98.765.432", "seat": "N", "dealer": "N", "vul": "", "auction": []}
    plan = plan_execution(ResearchKind.BEN, payload)
    assert plan.capability == "ben.compute"
    assert plan.assistant_lab_kind == "BEN_COMPUTE"
    assert plan.assistant_lab_payload == payload
    assert plan.execution_boundary == "assistant_lab_resident_worker_to_oracle_local_ben"


def test_research_key_is_deterministic_and_kind_scoped():
    left = canonical_research_key("DDS3", {"b": 2, "a": 1})
    right = canonical_research_key("DDS3", {"a": 1, "b": 2})
    other = canonical_research_key("BEN", {"a": 1, "b": 2})
    assert left == right
    assert left != other
    assert left.startswith(f"{RESEARCH_CONTRACT_VERSION}:")


def test_state_machine_requires_validation_before_completion():
    assert transition("QUEUED", "ACCEPTED") is ResearchStage.ACCEPTED
    assert transition("ACCEPTED", "RUNNING") is ResearchStage.RUNNING
    assert transition("RUNNING", "VALIDATING") is ResearchStage.VALIDATING
    assert transition("VALIDATING", "COMPLETED") is ResearchStage.COMPLETED
    with pytest.raises(LabContractError):
        transition("RUNNING", "COMPLETED")


def test_artifact_and_methodical_identity_are_bound():
    artifact = build_artifact_manifest(
        research_id="r-1",
        compute_result={"engine": "DDS3", "fallback_used": False},
        provenance={"worker": "oracle-assistant-lab-1"},
    )
    methodical = build_methodical_input(research_id="r-1", artifact_manifest=artifact)
    assert methodical["research_id"] == "r-1"
    assert methodical["canonical_promotion"] is False
    with pytest.raises(LabContractError):
        build_methodical_input(research_id="r-2", artifact_manifest=artifact)
