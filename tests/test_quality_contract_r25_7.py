#!/usr/bin/env python3
"""Regression tests for the r25.7 quality and self-improvement contract."""
import os

import bridge_runtime_hardening_r25_7 as r257


def test_model_contract():
    assert r257.validate_model_contract("medium", "medium") == "medium"
    try:
        r257.validate_model_contract("medium", "small")
    except RuntimeError as exc:
        assert "WHISPER_MODEL_MISMATCH" in str(exc)
    else:
        raise AssertionError("model mismatch was accepted")


def test_no_speech_is_explicit_and_conservative():
    silent = {
        "ok": False,
        "primaryTextEmpty": True,
        "failureReasons": ["EMPTY_CONTROL_ASR", "LOW_TEXT_SIMILARITY"],
        "qcEvidence": [
            {"text": ""},
            {"text": "you you you you you you you you you you"},
            {"text": ""},
        ],
        "riskBand": "CRITICAL",
    }
    assert r257.normalize_no_speech_qc([silent]) == 1
    assert silent["ok"] and silent["status"] == "NO_SPEECH"
    spoken = {
        "ok": False,
        "primaryTextEmpty": False,
        "qcEvidence": [{"text": "you you you you you you you you"}],
    }
    assert r257.normalize_no_speech_qc([spoken]) == 0
    assert not spoken["ok"]


def test_permission_principals_are_removed():
    matrix = r257.principal_free_permission_matrix([
        {"type": "user", "role": "reader", "emailAddress": "student@example.com"},
        {"type": "user", "role": "reader", "emailAddress": "other@example.com"},
        {"type": "user", "role": "writer", "displayName": "Teacher"},
        {"type": "user", "role": "owner", "emailAddress": "owner@example.com"},
    ])
    rendered = repr(matrix)
    assert "@" not in rendered and "principal" not in rendered and "Teacher" not in rendered
    assert any(item["role"] == "reader" and item["count"] == 2 for item in matrix)
    assert all(item["role"] != "owner" for item in matrix)


def test_hollow_counts_produce_partial_not_false_full():
    master = {
        "content_quality": {
            "transcript_segments": 740,
            "semantic_episodes": 282,
            "semantic_unresolved_candidates": 318,
        },
        "episodes": [{"episode_id": "e1"}],
        "deals": [{"status": "candidate", "auction": None, "contract": None}],
        "decisions": [{"observed_context": "x", "reasoning": None,
                       "decision_quality": "not rated without sufficient context"}],
        "learning_interactions": [{"student_action": None, "teacher_intervention": None,
                                   "student_response": None, "outcome": "requires review"}],
        "canon_links": [
            {"episode_id": f"e{i}", "score": 0.05, "canonical_excerpt": "same"}
            for i in range(20)
        ],
    }
    gate = r257.augment_quality_gate(master, {"ok": True})
    assert gate["ok"] is True
    assert gate["analysisCompletenessLevel"] == "PARTIAL"
    assert gate["completeDeals"] == 0
    assert "hollow-deal-candidates" in gate["qualityIssues"]
    assert "high-semantic-unresolved-ratio" in gate["qualityIssues"]


def test_canon_links_are_conservative_and_unique():
    episodes = [{"episode_id": "a"}, {"episode_id": "b"}, {"episode_id": "c"}]
    links = [
        {"episode_id": "a", "score": 0.05, "canonical_excerpt": "weak"},
        {"episode_id": "b", "score": 0.12, "canonical_excerpt": "same"},
        {"episode_id": "c", "score": 0.19, "canonical_excerpt": "same"},
    ]
    kept = r257.conservative_canon_links(links, episodes)
    assert len(kept) == 1 and kept[0]["episode_id"] == "c"
    assert episodes[0]["course_link_status"] == "не подтверждено"


def test_knowledge_status_never_overclaims():
    result = {"job_id": "a" * 32, "masterPdf": {"sha256": "b" * 64}}
    missing = r257.knowledge_status_payload(result, False)
    applied = r257.knowledge_status_payload(result, True)
    assert missing["status"] == "KNOWLEDGE_NOT_APPLIED" and missing["reason"]
    assert applied["status"] == "KNOWLEDGE_APPLIED" and applied["reason"] is None


if __name__ == "__main__":
    old_requested = os.environ.get("BRIDGE_REQUESTED_WHISPER_MODEL")
    old_effective = os.environ.get("WHISPER_MODEL")
    try:
        test_model_contract()
        test_no_speech_is_explicit_and_conservative()
        test_permission_principals_are_removed()
        test_hollow_counts_produce_partial_not_false_full()
        test_canon_links_are_conservative_and_unique()
        test_knowledge_status_never_overclaims()
    finally:
        if old_requested is None:
            os.environ.pop("BRIDGE_REQUESTED_WHISPER_MODEL", None)
        else:
            os.environ["BRIDGE_REQUESTED_WHISPER_MODEL"] = old_requested
        if old_effective is None:
            os.environ.pop("WHISPER_MODEL", None)
        else:
            os.environ["WHISPER_MODEL"] = old_effective
    print("R25_7_QUALITY_CONTRACT: PASS")
