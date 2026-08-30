import pytest

from evolutionary_course.methodology_queue import (
    MethodologyQueueError,
    build_methodology_review_queue,
    record_methodology_decision,
)


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
