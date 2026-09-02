#!/usr/bin/env python3
"""Regression tests for conservative r25.11 content and evidence gates."""
import uuid

import bridge_runtime_hardening_r25_11 as runtime
from database.video_result_persistence import _domain_rows, _evidence_rows


def episode(episode_id, text, *, cues=None, teacher=None, errors=None, evidence=None):
    return {
        "episode_id": episode_id,
        "type": "розыгрыш",
        "summary_text": text,
        "decision_cues": cues or [],
        "teacher_cues": teacher or [],
        "error_cues": errors or [],
        "question_cues": ["почему"] if "почему" in text.lower() else [],
        "terms": ["импас"],
        "evidence": evidence or [f"segment-{episode_id}"],
        "visual_evidence": [],
        "speaker": None,
    }


def test_substring_mentions_are_not_decisions():
    episodes = [
        episode("e1", "Какой импас проводить?", cues=["пас"]),
        episode("e2", "Лучшая атака против мастевого контракта.", cues=["контра"]),
    ]
    _, decisions = runtime.derive_deals_decisions(episodes, "job")
    assert decisions == []
    assert all(
        item["decision_extraction_audit"]["reason"] == "NO_EXACT_CUE_BOUNDARY"
        for item in episodes
    )


def test_explicit_choice_has_distinct_reasoning_clause():
    episodes = [episode(
        "e1",
        "Нужно сыграть импас, потому что торговля локализует даму справа. "
        "Вместо игры сверху можно было отложить решение.",
        cues=["импас"],
    )]
    _, decisions = runtime.derive_deals_decisions(episodes, "job")
    assert len(decisions) == 1
    item = decisions[0]
    assert item["action_taken"]["text"].startswith("Нужно сыграть")
    assert item["reasoning"] == "торговля локализует даму справа."
    assert item["reasoning"] != item["action_taken"]["text"]
    assert item["alternatives"]
    assert item["content_completeness"] == "FULL"


def test_cycle_has_no_adjacency_fallback():
    episodes = [
        episode("e1", "Как играть эту масть?", cues=["играть"]),
        episode("e2", "Следующий случай начинается здесь."),
        episode("e3", "Ещё один соседний фрагмент."),
    ]
    original = [{
        "cycle_id": "c1", "focus_episode_id": "e1",
        "task_or_trigger": "Как играть эту масть?", "evidence": ["segment-e1"],
        "student_action": None, "teacher_intervention": None,
        "student_response": None, "outcome": "требует проверки",
    }]
    cycle = runtime._enrich_cycles(original, episodes)[0]
    assert cycle["verification_status"] == "CANDIDATE_ONLY"
    assert cycle["role_neutral_sequence"]["instructional_response"] is None
    assert not runtime._complete_cycle(cycle)


def test_evidenced_role_neutral_sequence_is_not_actor_attribution():
    episodes = [
        episode("e1", "Как играть импас? Нужно сыграть импас.", cues=["импас"]),
        episode("e2", "Обратите внимание: нужно проверить торговлю.", teacher=["обратите внимание"]),
        episode("e3", "Теперь дама локализована справа."),
    ]
    original = [{
        "cycle_id": "c1", "focus_episode_id": "e1",
        "task_or_trigger": "Как играть импас?", "evidence": ["segment-e1"],
        "student_action": None, "teacher_intervention": None,
        "student_response": None, "outcome": "требует проверки",
    }]
    cycle = runtime._enrich_cycles(original, episodes)[0]
    assert cycle["verification_status"] == "VERIFIED_ROLE_NEUTRAL_SEQUENCE"
    assert cycle["attribution_status"] == "unavailable_without_speaker_labels"
    assert cycle["content_completeness"] == "EVIDENCE_COMPLETE_ROLE_NEUTRAL"
    assert runtime._complete_cycle(cycle)


def test_domain_rows_reference_real_evidence_ids():
    master = {
        "transcript": [{
            "segment_id": "segment-e1", "start": 10, "end": 20,
            "text": "Нужно сыграть импас.", "unreliable": False,
        }],
        "episodes": [{
            "episode_id": "e1", "type": "розыгрыш", "start": 10, "end": 20,
            "summary_text": "Нужно сыграть импас.", "evidence": ["segment-e1"],
        }],
        "learning_interactions": [],
        "decisions": [{
            "decision_id": "d1", "verification_status": "OBSERVED_DECISION",
            "action_taken": {"text": "Нужно сыграть импас", "cues": ["импас"]},
            "available_information": {"bridge_terms": ["импас"]},
            "reasoning": "дама справа", "evidence": ["segment-e1"],
            "content_completeness": "FULL",
        }],
    }
    run_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    transcript_id = uuid.UUID("00000000-0000-0000-0000-000000000002")
    source_id = uuid.UUID("00000000-0000-0000-0000-000000000003")
    asset_id = uuid.UUID("00000000-0000-0000-0000-000000000004")
    _, episode_rows, decision_rows, _, _ = _domain_rows(master, run_id, transcript_id)
    evidence_rows = _evidence_rows(master, transcript_id, source_id, asset_id)
    evidence_id = evidence_rows[0][0]
    assert episode_rows[0][-2] == [evidence_id]
    assert decision_rows[0][-1] == [evidence_id]


def test_quality_gate_reports_rejections_and_missing_speakers():
    episodes = [episode("e1", "Какой импас?", cues=["пас"])]
    runtime.derive_deals_decisions(episodes, "job")
    master = {
        "content_quality": {"transcript_segments": 10, "semantic_episodes": 1},
        "episodes": episodes, "deals": [], "decisions": [],
        "learning_interactions": [{
            "verification_status": "CANDIDATE_ONLY", "evidence": ["s1"],
            "attribution_status": "unavailable_without_speaker_labels",
            "role_neutral_sequence": {},
        }],
    }
    gate = runtime.augment_quality_gate(master, {"ok": True})
    assert gate["falsePositiveDecisionsRejected"] == 1
    assert gate["analysisCompletenessLevel"] == "PARTIAL"
    assert "speaker-attribution-unavailable" in gate["qualityIssues"]


if __name__ == "__main__":
    test_substring_mentions_are_not_decisions()
    test_explicit_choice_has_distinct_reasoning_clause()
    test_cycle_has_no_adjacency_fallback()
    test_evidenced_role_neutral_sequence_is_not_actor_attribution()
    test_domain_rows_reference_real_evidence_ids()
    test_quality_gate_reports_rejections_and_missing_speakers()
    print("R25_11_CONTENT_EVIDENCE: PASS")
