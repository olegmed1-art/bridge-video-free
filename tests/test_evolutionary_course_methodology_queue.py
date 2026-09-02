from copy import deepcopy
import json
from pathlib import Path

import pytest

from evolutionary_course.methodology_queue import (
    MethodologyQueueError,
    apply_approved_candidate_to_catalog,
    build_candidate_review_request,
    build_methodology_review_queue,
    record_candidate_review_decision,
    record_methodology_decision,
)
from evolutionary_course.skill_catalog import validate_catalog


def _report():
    return {
        "source_job_id": "job-1",
        "rejected_interactions": [{
            "interaction_id": "interaction-1",
            "reason_codes": ["SKILL_WORDING_NOT_REVIEWED"],
            "review_candidate": {
                "task_wording": "Неизвестная учебная формулировка",
                "video_file_id": "video-1",
                "source_name": "synthetic.mp4",
                "start_seconds": 10,
                "end_seconds": 20,
                "transcript_segment_ids": ["s1", "s2"],
            },
        }],
        "authority": {
            "canonical_promotion_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
    }


def _catalog(review_state="APPROVED_CANDIDATE"):
    return {
        "schema": "school-skill-catalog-v1",
        "catalog_version": "SCHOOL SKILL CATALOG v1",
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "school_canon_activation_allowed": False,
            "curriculum_activation_allowed": False,
            "student_profile_write_allowed": False,
            "publication_allowed": False,
        },
        "skills": [{
            "skill_id": "candidate.skill.existing",
            "title": "Existing skill",
            "aliases": [],
            "prerequisite_skill_ids": [],
            "mastery_criteria": {
                "RECOGNIZED": ["r"], "SUPPORTED": ["s"],
                "INDEPENDENT": ["i"], "TRANSFERRED": ["t"],
            },
            "review_state": review_state,
        }],
    }


def test_queue_is_deterministic_and_deduplicated():
    queue = build_methodology_review_queue([_report(), _report()])
    assert queue["item_count"] == 1
    assert queue["items"][0]["queue_id"].startswith("methodology.review.")
    assert queue["authority"]["catalog_mutation_allowed"] is False


def test_missing_provenance_and_authority_escalation_fail_closed():
    report = _report()
    report["rejected_interactions"][0].pop("review_candidate")
    with pytest.raises(MethodologyQueueError, match="provenance required"):
        build_methodology_review_queue([report])
    report = _report()
    report["authority"]["curriculum_activation_allowed"] = True
    with pytest.raises(MethodologyQueueError, match="authority boundary"):
        build_methodology_review_queue([report])


def test_mapping_requires_reviewed_catalog_skill():
    item = build_methodology_review_queue([_report()])["items"][0]
    receipt = record_methodology_decision(
        item, decision="MAP_EXISTING_SKILL", catalog=_catalog(),
        target_skill_id="candidate.skill.existing",
    )
    assert receipt["catalog_mutated"] is False
    assert receipt["review_required"] is True
    with pytest.raises(MethodologyQueueError, match="reviewed catalog skill"):
        record_methodology_decision(
            item, decision="MAP_EXISTING_SKILL",
            catalog=_catalog("REVIEW_REQUIRED"),
            target_skill_id="candidate.skill.existing",
        )


def test_new_candidate_is_proposal_only():
    item = build_methodology_review_queue([_report()])["items"][0]
    receipt = record_methodology_decision(
        item, decision="PROPOSE_NEW_CANDIDATE", catalog=_catalog(),
        proposed_candidate_id="candidate.skill.new-proposal",
    )
    assert receipt["proposed_candidate_id"] == "candidate.skill.new-proposal"
    assert receipt["catalog_mutated"] is False
    assert receipt["school_canon_mutated"] is False


def _real_candidate_and_catalog():
    candidate = json.loads(Path(
        "data/research/evolutionary_course_diana2_club_split_skill_candidate_v1.json"
    ).read_text(encoding="utf-8"))
    catalog = json.loads(Path(
        "data/research/evolutionary_course_skill_catalog_v1.json"
    ).read_text(encoding="utf-8"))
    _skill = next(item for item in catalog["skills"] if item["skill_id"] == candidate["skill_id"])
    _skill["review_state"] = "REVIEW_REQUIRED"
    return candidate, catalog


def test_candidate_review_request_is_unsigned_deterministic_and_non_mutating():
    candidate, catalog = _real_candidate_and_catalog()
    request = build_candidate_review_request(candidate, catalog=catalog)
    assert request == build_candidate_review_request(candidate, catalog=catalog)
    assert request["status"] == "AWAITING_HUMAN_DECISION"
    assert request["decision_input"] == {
        "decision": None, "reviewer_id": None, "reviewer_authority": None,
        "reviewed_at": None, "rationale": None,
    }
    assert request["allowed_decisions"] == ["APPROVE", "REVISE", "REJECT"]
    assert request["evidence_summary"]["numerator"] == 44616
    assert all(value is False for value in request["authority"].values())
    committed = json.loads(Path(
        "data/research/evolutionary_course_diana2_methodology_review_request_v1.json"
    ).read_text(encoding="utf-8"))
    assert committed == request


@pytest.mark.parametrize("decision", ["APPROVE", "REVISE", "REJECT"])
def test_candidate_review_receipt_is_human_attributed_and_non_mutating(decision):
    candidate, catalog = _real_candidate_and_catalog()
    receipt = record_candidate_review_decision(
        candidate, catalog=catalog, decision=decision, reviewer_id="reviewer-17",
        reviewer_authority="AUTHORIZED_METHODOLOGY_REVIEWER",
        reviewed_at="2026-08-30T12:00:00+00:00", rationale="Evidence reviewed.",
    )
    assert receipt["candidate_sha256"]
    assert receipt["catalog_mutated"] is False
    assert receipt["curriculum_activated"] is False
    assert receipt["proposed_review_state"] == (
        "APPROVED_CANDIDATE" if decision == "APPROVE" else None
    )
    assert receipt["catalog_removal_proposed"] is (decision == "REJECT")


def test_candidate_review_rejects_tampered_math_and_catalog_binding():
    candidate, catalog = _real_candidate_and_catalog()
    candidate["independent_probability_check"]["splits"][0]["numerator"] += 1
    kwargs = dict(
        catalog=catalog, decision="APPROVE", reviewer_id="director",
        reviewer_authority="SCHOOL_DIRECTOR", reviewed_at="2026-08-30T12:00:00Z",
        rationale="Reviewed.",
    )
    with pytest.raises(MethodologyQueueError, match="probability evidence mismatch"):
        record_candidate_review_decision(candidate, **kwargs)
    candidate, catalog = _real_candidate_and_catalog()
    catalog["skills"][0]["mastery_criteria"]["SUPPORTED"] = ["Changed criterion"]
    with pytest.raises(MethodologyQueueError, match="criteria do not match"):
        record_candidate_review_decision(candidate, **{**kwargs, "catalog": catalog})

    candidate, catalog = _real_candidate_and_catalog()
    candidate["independent_probability_check"]["splits"][0]["probability"] = "0.67"
    with pytest.raises(MethodologyQueueError, match="probability evidence mismatch"):
        record_candidate_review_decision(candidate, **{**kwargs, "catalog": catalog})


@pytest.mark.parametrize("field,value,error", [
    ("reviewer_id", " ", "reviewer identity"),
    ("reviewer_authority", "BOT", "authorized reviewer"),
    ("reviewed_at", "2026-08-30T12:00:00", "timezone"),
    ("rationale", "", "rationale"),
])
def test_candidate_review_requires_attributed_authorized_human_decision(field, value, error):
    candidate, catalog = _real_candidate_and_catalog()
    kwargs = dict(
        catalog=catalog, decision="APPROVE", reviewer_id="director",
        reviewer_authority="SCHOOL_DIRECTOR", reviewed_at="2026-08-30T12:00:00Z",
        rationale="Reviewed.",
    )
    kwargs[field] = value
    with pytest.raises(MethodologyQueueError, match=error):
        record_candidate_review_decision(candidate, **kwargs)


def test_candidate_review_requires_review_required_state_and_human_gate():
    candidate, catalog = _real_candidate_and_catalog()
    catalog = deepcopy(catalog)
    catalog["skills"][0]["review_state"] = "APPROVED_CANDIDATE"
    with pytest.raises(MethodologyQueueError, match="review-required catalog skill"):
        record_candidate_review_decision(
            candidate, catalog=catalog, decision="APPROVE", reviewer_id="director",
            reviewer_authority="SCHOOL_DIRECTOR", reviewed_at="2026-08-30T12:00:00Z",
            rationale="Reviewed.",
        )


def test_exact_approved_receipt_applies_only_catalog_candidate_state():
    candidate, catalog = _real_candidate_and_catalog()
    receipt = json.loads(Path(
        "data/research/evolutionary_course_diana2_methodology_decision_receipt_v1.json"
    ).read_text(encoding="utf-8"))
    updated = apply_approved_candidate_to_catalog(
        candidate, catalog=catalog, receipt=receipt
    )
    changed = next(skill for skill in updated["skills"] if skill["skill_id"] == candidate["skill_id"])
    assert changed["review_state"] == "APPROVED_CANDIDATE"
    assert updated["authority"] == catalog["authority"]
    committed = json.loads(Path(
        "data/research/evolutionary_course_skill_catalog_v1.json"
    ).read_text(encoding="utf-8"))
    assert validate_catalog(committed) == updated


def test_tampered_or_non_approve_receipt_cannot_mutate_catalog():
    candidate, catalog = _real_candidate_and_catalog()
    receipt = json.loads(Path(
        "data/research/evolutionary_course_diana2_methodology_decision_receipt_v1.json"
    ).read_text(encoding="utf-8"))
    receipt["candidate_sha256"] = "0" * 64
    with pytest.raises(MethodologyQueueError, match="receipt mismatch"):
        apply_approved_candidate_to_catalog(candidate, catalog=catalog, receipt=receipt)
    receipt["decision"] = "REJECT"
    with pytest.raises(MethodologyQueueError, match="approved candidate receipt"):
        apply_approved_candidate_to_catalog(candidate, catalog=catalog, receipt=receipt)
