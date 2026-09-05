import json

from bridge_contracts.video_extended_extraction import build_extended_extraction


def test_rule_without_why_creates_explanation_gap():
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction({"job_id": "job-1"}, quality)
    gaps = [
        row for row in result["candidate_records"]
        if row["candidate_type"] == "GAP_OR_CONFLICT"
        and row["payload"].get("gap_type") == "EXPLANATION_MISSING"
    ]
    assert len(gaps) == 1
    assert gaps[0]["payload"]["rule_stable_key"] == "rule-7"
    assert gaps[0]["promotion_allowed"] is False


def test_nonfinite_extended_observation_becomes_bounded_gap():
    master = {
        "job_id": "job-1",
        "terminology_observations": [{
            "stable_key": "term:bad",
            "term": "форсинг",
            "score": float("nan"),
        }],
    }
    result = build_extended_extraction(
        master, {"authority": {"canon_activation": "DENY"}}
    )
    assert not any(
        row["candidate_type"] == "SCHOOL_TERMINOLOGY"
        for row in result["candidate_records"]
    )
    gap = next(
        row for row in result["candidate_records"]
        if row["payload"].get("gap_type") == "EXTENDED_OBSERVATION_INVALID"
    )
    assert gap["payload"]["source_candidate_type"] == "SCHOOL_TERMINOLOGY"
    json.dumps(result, allow_nan=False)


def test_corrected_explicit_observation_gets_content_addressed_identity():
    for field, candidate_type, stable_key in (
        ("terminology_observations", "SCHOOL_TERMINOLOGY", "term:forcing"),
        ("system_evolution_observations", "SYSTEM_EVOLUTION_OBSERVATION", "evolution:7"),
        ("world_comparison_links", "WORLD_COMPARISON_LINK", "world:7"),
    ):
        first_master = {
            "job_id": "job-1",
            field: [{"stable_key": stable_key, "definition": "old definition"}],
        }
        corrected_master = {
            **first_master,
            field: [{**first_master[field][0], "definition": "corrected definition"}],
        }
        quality = {"authority": {"canon_activation": "DENY"}}

        def observation(master):
            return next(
                row for row in build_extended_extraction(master, quality)["candidate_records"]
                if row["candidate_type"] == candidate_type
            )

        first = observation(first_master)
        corrected = observation(corrected_master)
        replay = observation(first_master)
        assert first["stable_key"].startswith(f"{stable_key}:sha256:")
        assert first["stable_key"] != corrected["stable_key"]
        assert first["candidate_id"] != corrected["candidate_id"]
        assert first["stable_key"] == replay["stable_key"]
        assert first["candidate_id"] == replay["candidate_id"]


def test_corrected_explicit_explanation_gets_content_addressed_identity():
    def extract(statement):
        master = {
            "job_id": "job-1",
            "transcript": [{
                "segment_id": "segment-7",
                "speaker_role": "teacher",
                "speaker_role_confidence": 0.99,
                "text": statement,
            }],
            "explanation_observations": [{
                "stable_key": "why:rule-7",
                "rule_stable_key": "rule-7",
                "why_chain": [statement],
                "evidence_refs": ["segment-7"],
            }],
        }
        quality = {
            "canon_candidates": [{
                "canon_observation_id": "rule-7",
                "classification": "RULE_PARAPHRASE_MATCH",
                "evidence_refs": ["segment-7"],
            }],
            "authority": {"canon_activation": "DENY"},
        }
        return next(
            row for row in build_extended_extraction(master, quality)["candidate_records"]
            if row["candidate_type"] == "EXPLANATION_CANDIDATE"
        )

    first = extract("original explanation")
    corrected = extract("corrected explanation")
    replay = extract("original explanation")
    assert first["stable_key"].startswith("why:rule-7:sha256:")
    assert first["stable_key"] != corrected["stable_key"]
    assert first["candidate_id"] != corrected["candidate_id"]
    assert first["stable_key"] == replay["stable_key"]
    assert first["candidate_id"] == replay["candidate_id"]


def test_nonfinite_explicit_explanation_becomes_bounded_gap():
    master = {
        "job_id": "job-1",
        "transcript": [{
            "segment_id": "segment-7",
            "speaker_role": "teacher",
            "speaker_role_confidence": 0.99,
            "text": "source-bound explanation",
        }],
        "explanation_observations": [{
            "stable_key": "why:rule-7",
            "rule_stable_key": "rule-7",
            "why_chain": ["source-bound explanation"],
            "example": {"score": float("nan")},
            "evidence_refs": ["segment-7"],
        }],
    }
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction(master, quality)
    assert not any(
        row["candidate_type"] == "EXPLANATION_CANDIDATE"
        for row in result["candidate_records"]
    )
    gaps = [
        row for row in result["candidate_records"]
        if row["payload"].get("gap_type") in {
            "EXPLANATION_EVIDENCE_INVALID", "EXPLANATION_MISSING",
        }
    ]
    assert {row["payload"]["gap_type"] for row in gaps} == {
        "EXPLANATION_EVIDENCE_INVALID", "EXPLANATION_MISSING",
    }
    invalid_gap = next(
        row for row in gaps
        if row["payload"]["gap_type"] == "EXPLANATION_EVIDENCE_INVALID"
    )
    assert invalid_gap["evidence_refs"] == []
    json.dumps(result, allow_nan=False)


def test_source_bound_why_is_a_separate_explanation_candidate():
    master = {
        "job_id": "job-1",
        "transcript": [{
            "segment_id": "segment-7", "speaker_role": "teacher",
            "speaker_role_confidence": 0.99,
            "text": "premise conclusion",
        }],
        "explanation_observations": [{
            "stable_key": "why:rule-7",
            "rule_stable_key": "rule-7",
            "why_chain": ["premise", "conclusion"],
            "rejected_alternatives": [{"action": "PASS", "reason": "forcing"}],
            "example": {"auction": ["1H", "2C"]},
            "counterexample": {"auction": ["1H", "X"]},
            "evidence_refs": ["segment-7"],
            "status": "REVIEW_REQUIRED",
        }],
    }
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction(master, quality)
    assert sum(row["candidate_type"] == "EXPLANATION_CANDIDATE" for row in result["candidate_records"]) == 1
    assert not any(
        row["payload"].get("gap_type") == "EXPLANATION_MISSING"
        for row in result["candidate_records"]
    )


def test_extracts_explicit_causal_teacher_speech_from_real_analysis_shape():
    master = {
        "job_id": "job-1",
        "transcript": [{
            "segment_id": "segment-7",
            "speaker": "teacher:diana",
            "speaker_role": "teacher",
            "speaker_role_confidence": 0.96,
            "start": 10.0,
            "end": 14.0,
            "text": "Мы не пасуем, потому что эта заявка форсирует.",
        }],
    }
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction(master, quality)
    explanation = next(
        row for row in result["candidate_records"]
        if row["candidate_type"] == "EXPLANATION_CANDIDATE"
    )
    assert explanation["payload"]["extraction_class"] == "EXPLICIT_CAUSAL_TEACHER_STATEMENT"
    assert explanation["payload"]["why_chain"] == [
        "Мы не пасуем, потому что эта заявка форсирует."
    ]
    assert explanation["payload"]["generated_rationale_allowed"] is False
    assert explanation["payload"]["logic_relations"] == [{
        "relation_type": "CAUSE",
        "cue": "потому что",
        "left_clause": "Мы не пасуем",
        "right_clause": "эта заявка форсирует",
    }]
    assert result["explanation_extraction"]["automatic_explicit_causal"] == 1
    assert not any(
        row["payload"].get("gap_type") == "EXPLANATION_MISSING"
        for row in result["candidate_records"]
    )


def test_does_not_treat_student_or_low_confidence_causal_speech_as_teacher_why():
    for role, confidence in (("student", 0.99), ("teacher", 0.79)):
        master = {
            "job_id": "job-1",
            "transcript": [{
                "segment_id": "segment-7", "speaker_role": role,
                "speaker_role_confidence": confidence,
                "text": "Потому что это форсирует.",
            }],
        }
        quality = {
            "canon_candidates": [{
                "canon_observation_id": "rule-7",
                "classification": "RULE_PARAPHRASE_MATCH",
                "evidence_refs": ["segment-7"],
            }],
            "authority": {"canon_activation": "DENY"},
        }
        result = build_extended_extraction(master, quality)
        assert not any(
            row["candidate_type"] == "EXPLANATION_CANDIDATE"
            for row in result["candidate_records"]
        )
        assert any(
            row["payload"].get("gap_type") == "EXPLANATION_MISSING"
            for row in result["candidate_records"]
        )


def test_invalid_speaker_confidence_cannot_suppress_explanation_gap():
    for confidence in ("NaN", float("nan"), float("inf"), True, 10**1000):
        master = {"job_id": "job", "transcript": [{
            "segment_id": "segment-7", "speaker_role": "teacher",
            "speaker_role_confidence": confidence,
            "text": "Не пасуем, потому что форсинг.",
        }]}
        quality = {"canon_candidates": [{
            "canon_observation_id": "rule-7", "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }], "authority": {"canon_activation": "DENY"}}
        result = build_extended_extraction(master, quality)
        assert any(row["payload"].get("gap_type") == "EXPLANATION_MISSING"
                   for row in result["candidate_records"])


def test_extracts_why_and_what_for_as_distinct_logic_relations():
    master = {
        "job_id": "job-purpose",
        "transcript": [{
            "segment_id": "segment-purpose",
            "speaker_role": "teacher",
            "speaker_role_confidence": 0.95,
            "text": "Мы делаем трансфер, чтобы передать право выбора партнёру.",
        }],
    }
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-purpose",
            "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-purpose"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    result = build_extended_extraction(master, quality)
    explanation = next(
        row["payload"] for row in result["candidate_records"]
        if row["candidate_type"] == "EXPLANATION_CANDIDATE"
    )
    assert explanation["explanation_dimensions"] == ["PURPOSE"]
    assert explanation["logic_relations"][0] == {
        "relation_type": "PURPOSE",
        "cue": "чтобы",
        "left_clause": "Мы делаем трансфер",
        "right_clause": "передать право выбора партнёру",
    }
    assert "CAUSE" in result["explanation_extraction"]["logic_dimensions"]
    assert "PURPOSE" in result["explanation_extraction"]["logic_dimensions"]


def test_logic_cues_require_token_boundaries_and_use_adjacent_clauses():
    quality = {
        "canon_candidates": [{
            "canon_observation_id": "rule-7", "classification": "RULE_PARAPHRASE_MATCH",
            "evidence_refs": ["segment-7"],
        }],
        "authority": {"canon_activation": "DENY"},
    }
    master = {"job_id": "job", "transcript": [{
        "segment_id": "segment-7", "speaker_role": "teacher",
        "speaker_role_confidence": 0.99,
        "text": "Нужно назначить масть.",
    }]}
    result = build_extended_extraction(master, quality)
    assert not any(row["candidate_type"] == "EXPLANATION_CANDIDATE" for row in result["candidate_records"])

    master["transcript"][0]["text"] = "Не пасуем, потому что форсинг. Баланс есть, поэтому заявляем гейм."
    result = build_extended_extraction(master, quality)
    explanation = next(row["payload"] for row in result["candidate_records"] if row["candidate_type"] == "EXPLANATION_CANDIDATE")
    assert explanation["logic_relations"][0]["right_clause"] == "форсинг"
    assert explanation["logic_relations"][1]["left_clause"] == "Баланс есть"


def test_unbound_explicit_explanation_becomes_gap_and_does_not_hide_missing_why():
    master = {"job_id": "job", "explanation_observations": [{
        "stable_key": "why:rule-7", "rule_stable_key": "rule-7",
        "why_chain": ["generated guess"], "evidence_refs": ["missing-segment"],
    }]}
    quality = {"canon_candidates": [{
        "canon_observation_id": "rule-7", "classification": "RULE_PARAPHRASE_MATCH",
        "evidence_refs": ["segment-7"],
    }], "authority": {"canon_activation": "DENY"}}
    result = build_extended_extraction(master, quality)
    gap_types = {row["payload"].get("gap_type") for row in result["candidate_records"]}
    assert "EXPLANATION_EVIDENCE_INVALID" in gap_types
    assert "EXPLANATION_MISSING" in gap_types


def test_retrieval_or_gap_classification_does_not_create_fake_explanation_gap():
    quality = {"canon_candidates": [{
        "canon_observation_id": "lookup-1", "classification": "NO_CANON_MATCH",
        "evidence_refs": ["segment-7"],
    }], "authority": {"canon_activation": "DENY"}}
    result = build_extended_extraction({"job_id": "job"}, quality)
    assert not any(row["payload"].get("gap_type") == "EXPLANATION_MISSING" for row in result["candidate_records"])
