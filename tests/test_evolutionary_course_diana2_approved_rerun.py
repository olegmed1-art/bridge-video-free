import json
from pathlib import Path

from evolutionary_course.skill_catalog import resolve_reviewed_skill


def test_diana2_approved_rerun_is_private_non_persisting_episode_candidate():
    receipt = json.loads(Path(
        "data/research/evolutionary_course_diana2_approved_rerun_receipt_v1.json"
    ).read_text(encoding="utf-8"))
    assert receipt["status"] == "READY_FOR_PRIVATE_REVIEW"
    assert receipt["result"]["blockers"] == []
    assert receipt["result"]["accepted_episode_count"] == 1
    assert receipt["result"]["rejected_interaction_count"] == 103
    assert receipt["result"]["episode_review_state"] == "APPROVED_CANDIDATE"
    assert receipt["result"]["mastery_transition"] == {
        "from_state": "INTRODUCED", "to_state": "INTRODUCED"
    }
    assert receipt["effects"]["episode_built_in_memory"] is True
    assert receipt["effects"]["episode_persisted"] is False
    assert all(value is False for value in receipt["authority"].values())


def test_diana2_episode_skill_resolves_from_approved_catalog():
    catalog = json.loads(Path(
        "data/research/evolutionary_course_skill_catalog_v1.json"
    ).read_text(encoding="utf-8"))
    receipt = json.loads(Path(
        "data/research/evolutionary_course_diana2_approved_rerun_receipt_v1.json"
    ).read_text(encoding="utf-8"))
    wording = "Какие у нас шансы, примерно? А какие у нас шансы разыграть трефу?"
    assert resolve_reviewed_skill(catalog, wording) == receipt["result"]["skill_id"]
