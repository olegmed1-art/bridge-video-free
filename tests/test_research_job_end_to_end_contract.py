from assistant_lab.contract import LabContractError, validate_job_payload, verify_ben_result
from assistant_lab.research_pipeline import (
    ResearchKind,
    build_artifact_manifest,
    build_methodical_result,
    plan_execution,
    verify_artifact_manifest,
)


def test_ben_plan_uses_resident_worker_child():
    payload = {"hand": "AK97543.K.T3.AK7", "seat": "S", "dealer": "N", "vul": "", "auction": []}
    plan = plan_execution(ResearchKind.BEN, payload)
    assert plan.capability == "ben.compute"
    assert plan.assistant_lab_kind == "BEN_COMPUTE"
    assert plan.execution_boundary.endswith("oracle_local_ben")


def test_ben_payload_is_bounded():
    payload = {"hand": "AKQ.JT9.876.543", "seat": "N", "dealer": "E", "auction": ["1C", "PASS"]}
    assert validate_job_payload("BEN_COMPUTE", payload)["seat"] == "N"
    try:
        validate_job_payload("BEN_COMPUTE", {**payload, "auction": ["PASS"] * 81})
    except LabContractError:
        pass
    else:
        raise AssertionError("unbounded BEN auction accepted")


def test_ben_result_stays_policy_only():
    result = verify_ben_result({"bid": "1S", "candidates": [{"call": "1S", "insta_score": 1.2}]})
    assert result["engine"] == "BEN"
    assert result["evidence_class"] == "POLICY_ONLY"
    assert result["dds_search_evidence"] is False


def test_artifact_checksum_and_methodical_derivative_are_bound():
    artifact = build_artifact_manifest(
        research_id="r-1",
        compute_result={"engine": "DDS3", "fallback_used": False, "operation": "dd_table"},
        provenance={"worker_id": "oracle-assistant-lab-1"},
    )
    assert verify_artifact_manifest(artifact)["sha256"] == artifact["sha256"]
    methodical = build_methodical_result(research_id="r-1", artifact_manifest=artifact)
    assert methodical["evidence_sha256"] == artifact["sha256"]
    assert methodical["canonical_promotion"] is False


def test_artifact_tamper_fails_closed():
    artifact = build_artifact_manifest(research_id="r-2", compute_result={"x": 1}, provenance={})
    artifact["compute_result"]["x"] = 2
    try:
        verify_artifact_manifest(artifact)
    except LabContractError:
        pass
    else:
        raise AssertionError("tampered artifact accepted")
