#!/usr/bin/env python3
"""Pure regression tests for r25.10 content and terminal contracts."""
import bridge_runtime_hardening_r25_10 as runtime


def episode(episode_id, text, *, decisions=None, teacher=None, errors=None, speaker=None):
    return {
        "episode_id": episode_id,
        "type": "розыгрыш",
        "summary_text": text,
        "decision_cues": decisions or [],
        "teacher_cues": teacher or [],
        "error_cues": errors or [],
        "question_cues": ["почему"] if "почему" in text.lower() else [],
        "terms": ["импас"],
        "evidence": [f"seg-{episode_id}"],
        "visual_evidence": [],
        "speaker": speaker,
    }


def test_decision_extracts_action_reason_and_information():
    episodes = [episode(
        "e1",
        "Нужно сделать импас, потому что торговля локализует даму справа. "
        "Вместо игры сверху можно отложить решение.",
        decisions=["импас"],
    )]
    _, decisions = runtime.derive_deals_decisions(episodes, "job")
    assert len(decisions) == 1
    item = decisions[0]
    assert item["action_taken"]["text"]
    assert item["reasoning"]
    assert item["available_information"]["bridge_terms"] == ["импас"]
    assert item["alternatives"]
    assert item["content_completeness"] == "FULL"
    assert item["actor"] is None


def test_role_neutral_cycle_does_not_invent_people():
    episodes = [
        episode("e1", "Как разыгрывать эту масть?", decisions=["разыгрывать"]),
        episode("e2", "Обратите внимание на торговлю и первый ход.", teacher=["обратите внимание"]),
        episode("e3", "Значит, даму надо искать справа.", decisions=["положить"]),
    ]
    original = [{
        "cycle_id": "c1",
        "focus_episode_id": "e1",
        "task_or_trigger": "Как разыгрывать эту масть?",
        "student_action": None,
        "teacher_intervention": None,
        "student_response": None,
        "outcome": "требует проверки",
        "evidence": ["seg-e1"],
    }]
    cycle = runtime._enrich_cycles(original, episodes)[0]
    assert cycle["content_completeness"] == "FULL"
    assert cycle["attribution_status"] == "unavailable_without_speaker_labels"
    assert cycle["student_action"] is None
    assert runtime._complete_cycle(cycle)


def test_empty_hands_are_not_a_complete_deal():
    hollow = {"hands": {"N": None, "E": None, "S": None, "W": None}}
    assert not runtime._complete_deal(hollow)
    assert runtime._complete_deal(dict(hollow, contract="3NT"))


def test_quality_contract_separates_content_from_attribution():
    decision = {
        "observed_context": "x",
        "action_taken": {"text": "пас"},
        "available_information": {"auction": "known"},
        "reasoning": "потому что рука минимальная",
        "evidence": ["s1"],
        "actor": None,
    }
    cycle = {
        "role_neutral_sequence": {
            "observed_action": "пас",
            "instructional_response": "проверим силу",
            "observed_followup": "понятно",
        },
        "attribution_status": "unavailable_without_speaker_labels",
    }
    master = {
        "content_quality": {"transcript_segments": 10, "semantic_episodes": 3},
        "episodes": [{"episode_id": "e1"}],
        "deals": [],
        "decisions": [decision],
        "learning_interactions": [cycle],
        "canon_links": [],
    }
    gate = runtime.augment_quality_gate(master, {"ok": True})
    assert gate["contentCompleteDecisions"] == 1
    assert gate["actorAttributedDecisions"] == 0
    assert gate["contentCompleteLearningCycles"] == 1
    assert gate["actorAttributedLearningCycles"] == 0


def test_knowledge_status_requires_confirmed_commit():
    result = {"job_id": "a" * 32, "masterPdf": {"sha256": "b" * 64}}
    missing = runtime._knowledge_status(result, None)
    applied = runtime._knowledge_status(result, {
        "persisted": True,
        "analysis_run_id": "run",
        "transcript_id": "transcript",
        "episodes": 3,
        "learning_cycles": 1,
        "decisions": 1,
    })
    assert missing["status"] == "KNOWLEDGE_NOT_APPLIED"
    assert applied["status"] == "KNOWLEDGE_APPLIED"
    assert applied["database"]["decisions"] == 1


if __name__ == "__main__":
    test_decision_extracts_action_reason_and_information()
    test_role_neutral_cycle_does_not_invent_people()
    test_empty_hands_are_not_a_complete_deal()
    test_quality_contract_separates_content_from_attribution()
    test_knowledge_status_requires_confirmed_commit()
    print("R25_10_CONTENT_PERSISTENCE: PASS")
