#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path

import bridge_runtime_hardening_r25_12_meta as candidate


FIXTURE = Path(__file__).parent / "fixtures" / "asr_block_11_r25_11.json"


def test_block_11_hallucination_is_permanent_hard_stop_and_is_not_rewritten():
    record = json.loads(FIXTURE.read_text(encoding="utf-8"))
    before = copy.deepcopy(record)
    assert candidate.normalize_no_speech_qc([record]) == 0
    for key in ("ok", "similarity", "failureReasons", "estimatedErrorRisk", "riskBand"):
        assert record[key] == before[key]
    assert record["noSpeechClassification"] == "REJECTED_HALLUCINATION"
    gate = candidate.independent_asr_evidence_gate(
        [record], base_coverage_passed=True, unreliable_derived_evidence_count=0
    )
    assert gate["workerEvidenceStatus"] == "FAIL"
    assert gate["hallucinationBlocks"] == [11]
    assert not gate["publicationAllowed"]


def test_isolated_zero_without_hallucination_is_quarantined_not_erased():
    record = {
        "block": 4,
        "primaryTextEmpty": True,
        "ok": False,
        "similarity": 0.0,
        "failureReasons": ["EMPTY_CONTROL_ASR", "LOW_TEXT_SIMILARITY"],
        "estimatedErrorRisk": 0.84,
        "riskBand": "HIGH",
        "qcEvidence": [{"text": ""}],
    }
    before = copy.deepcopy(record)
    assert candidate.normalize_no_speech_qc([record]) == 1
    for key in ("ok", "similarity", "failureReasons", "estimatedErrorRisk", "riskBand"):
        assert record[key] == before[key]
    gate = candidate.independent_asr_evidence_gate(
        [record], base_coverage_passed=True, unreliable_derived_evidence_count=0
    )
    assert gate["workerEvidenceStatus"] == "PASS_CANDIDATE"
    assert gate["isolatedZeroBlocks"] == [4]
    assert record["excludedFromDerivedEvidence"] is True
    assert gate["independentAssessmentRequired"] is True
    assert gate["publicationAllowed"] is False


def test_unreliable_derived_evidence_is_always_a_stop():
    record = {"block": 0, "ok": True, "similarity": 0.92}
    gate = candidate.independent_asr_evidence_gate(
        [record], base_coverage_passed=True, unreliable_derived_evidence_count=1
    )
    assert gate["workerEvidenceStatus"] == "FAIL"
    assert "UNRELIABLE_DERIVED_EVIDENCE" in gate["failureReasons"]


def test_public_product_name_is_unchanged():
    import bridge_worker_3_1_free as core
    assert core.ALGORITHM_VERSION == "3.1 FREE"


def test_candidate_persistence_is_fail_closed_and_evented():
    source = (Path(__file__).parents[1] / "database" / "video_result_persistence.py").read_text(
        encoding="utf-8"
    )
    assert 'candidate_requires_meta = algorithm_revision == "3.1-free-r25.12-meta"' in source
    assert "CASE WHEN %s THEN 'running' ELSE 'success' END" in source
    assert "algorithm_version_id" in source
    assert "BridgeVideoResultRecorded" in source
    assert "INSERT INTO public.changeset" in source
    assert '"publication_authorization_status": "blocked"' in source


if __name__ == "__main__":
    test_block_11_hallucination_is_permanent_hard_stop_and_is_not_rewritten()
    test_isolated_zero_without_hallucination_is_quarantined_not_erased()
    test_unreliable_derived_evidence_is_always_a_stop()
    test_public_product_name_is_unchanged()
    test_candidate_persistence_is_fail_closed_and_evented()
    print("R25_12_META_ASR_GATE: PASS")
