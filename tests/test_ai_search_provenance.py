from uuid import uuid4

from bridge_school_api.ai_worker import CandidateEvaluation, _has_explicit_search_provenance


def _evaluation(metrics):
    return CandidateEvaluation(candidate_id=uuid4(), raw_score_ev=1.0, metrics_json=metrics)


def test_completed_search_accepts_real_engine_provenance():
    assert _has_explicit_search_provenance(_evaluation({
        "evidence_class": "DDS3_DOUBLE_DUMMY",
        "engine": "DDS3",
        "fallback_used": False,
    }))
    assert _has_explicit_search_provenance(_evaluation({
        "evidence_class": "BEN_SIMULATION",
        "engine": "BEN",
        "fallback_used": False,
    }))


def test_completed_search_rejects_policy_or_fallback_as_search():
    assert not _has_explicit_search_provenance(_evaluation({
        "evidence_class": "BEN_POLICY",
        "engine": "BEN",
        "fallback_used": False,
    }))
    assert not _has_explicit_search_provenance(_evaluation({
        "evidence_class": "DDS3_DOUBLE_DUMMY",
        "engine": "DDS3",
        "fallback_used": True,
    }))
    assert not _has_explicit_search_provenance(CandidateEvaluation(
        candidate_id=uuid4(),
        metrics_json={
            "evidence_class": "DDS3_DOUBLE_DUMMY",
            "engine": "BEN",
            "fallback_used": False,
        },
    ))
