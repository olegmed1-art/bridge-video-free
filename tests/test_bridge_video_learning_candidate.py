from __future__ import annotations

from copy import deepcopy

import pytest

from bridge_contracts.video_learning_candidate import (
    LearningCandidateError,
    canonical_sha256,
    validate_learning_candidate,
)


def _candidate() -> dict:
    transcript_sha = "b" * 64
    frame_sha = "c" * 64
    locator = "transcript.jsonl#segment=7"
    return {
        "schema": "video31-learning-candidate-v1",
        "status": "CANDIDATE_RESEARCH",
        "candidate_id": "canary.interaction.7",
        "observed_episode": {
            "interaction_id": "interaction-7",
            "start": 9.0,
            "end": 12.0,
            "task": "Определить следующую заявку.",
            "student_action": "Ученица предлагает заявку.",
            "teacher_intervention": "Преподаватель просит проверить баланс.",
            "student_followup": "Ученица пересматривает ответ.",
            "observed_outcome": "Ответ изменён после вопроса.",
            "actor_attribution_status": "SUPPORTED",
        },
        "source": {
            "video_file_id": "drive-file-canary",
            "source_name": "canary-lesson.mp4",
            "source_sha256": "a" * 64,
            "source_fingerprint": "source-canary",
        },
        "transcript_evidence": [{
            "locator": locator,
            "start": 9.0,
            "end": 11.0,
            "text_sha256": transcript_sha,
            "speaker_id": "UNKNOWN",
            "speaker_identity_status": "UNKNOWN",
        }],
        "frame_evidence": [{
            "schema": "bridge-speech-frame-binding-v1",
            "method": "EXPLICIT_FRAME_SHA256",
            "frame_sha256": frame_sha,
            "frame_file": "frame-00010.jpg",
            "frame_time": 10.0,
            "speech_start": 9.0,
            "speech_end": 11.0,
            "transcript_locator": locator,
            "distance_to_midpoint_seconds": 0.0,
            "source_fingerprint": "source-canary",
            "single_frame_binding": True,
        }],
        "bridge_context": {
            "board": {"status": "CONFIRMED", "value": 13, "source_refs": [frame_sha]},
            "dealer": {"status": "UNKNOWN", "value": None, "source_refs": []},
            "vulnerability": {"status": "UNKNOWN", "value": None, "source_refs": []},
            "auction": {"status": "REVIEW", "value": ["1H", "?"], "source_refs": [frame_sha]},
            "deal": {"status": "UNKNOWN", "value": None, "source_refs": []},
        },
        "preliminary_skill": {"label": "Проверка баланса перед заявкой", "status": "PROPOSED"},
        "confidence": {
            "transcript": 0.98,
            "frame": 1.0,
            "actor_attribution": 0.8,
            "bridge_context": 0.4,
            "preliminary_skill": 0.55,
        },
        "provenance": {
            "algorithm_revision": "3.1-test-r7-source-bound-speech",
            "contract_version": "video31-learning-candidate-v1",
        },
        "unresolved_questions": [
            "Кто сдающий?",
            "Какова зональность?",
            "Можно ли подтвердить полную торговлю и раздачу?",
        ],
        "authority": {
            "authority_class": "CANDIDATE_RESEARCH",
            "school_canon_write_allowed": False,
            "student_profile_write_allowed": False,
            "approved_course_write_allowed": False,
            "publication_allowed": False,
        },
    }


def test_accepts_reproducible_research_candidate_and_is_deterministic():
    candidate = _candidate()
    assert validate_learning_candidate(candidate) == candidate
    assert canonical_sha256(candidate) == canonical_sha256(deepcopy(candidate))


@pytest.mark.parametrize("mutation, match", [
    (lambda value: value["frame_evidence"].clear(), "frame evidence required"),
    (lambda value: value["frame_evidence"][0].update(single_frame_binding=False), "exactly one frame"),
    (lambda value: value["frame_evidence"][0].update(source_fingerprint="other"), "source mismatch"),
    (lambda value: value["frame_evidence"][0].update(transcript_locator="other"), "unknown transcript"),
    (lambda value: value["frame_evidence"][0].update(frame_time=11.5), "outside bound speech"),
    (lambda value: value["frame_evidence"][0].update(speech_start=9.5), "differs from transcript"),
    (lambda value: value["frame_evidence"][0].update(distance_to_midpoint_seconds=0.1), "distance mismatch"),
    (lambda value: value["frame_evidence"][0].update(method="GUESS"), "unsupported frame binding method"),
    (lambda value: value["observed_episode"].update(actor_attribution_status="UNPROVEN"), "attribution unproven"),
])
def test_rejects_unbound_or_unproven_evidence(mutation, match):
    candidate = _candidate()
    mutation(candidate)
    with pytest.raises(LearningCandidateError, match=match):
        validate_learning_candidate(candidate)


def test_unknown_speaker_cannot_acquire_an_unverified_name():
    candidate = _candidate()
    candidate["transcript_evidence"][0]["speaker_id"] = "Named Student"
    with pytest.raises(LearningCandidateError, match="must remain UNKNOWN"):
        validate_learning_candidate(candidate)


def test_unknown_bridge_values_cannot_be_inferred_or_hide_open_questions():
    candidate = _candidate()
    candidate["bridge_context"]["dealer"]["value"] = "N"
    with pytest.raises(LearningCandidateError, match="must not carry inferred data"):
        validate_learning_candidate(candidate)

    candidate = _candidate()
    candidate["unresolved_questions"] = []
    with pytest.raises(LearningCandidateError, match="requires questions"):
        validate_learning_candidate(candidate)


def test_authority_cannot_escalate_to_profile_course_canon_or_publication():
    for field in (
        "school_canon_write_allowed", "student_profile_write_allowed",
        "approved_course_write_allowed", "publication_allowed",
    ):
        candidate = _candidate()
        candidate["authority"][field] = True
        with pytest.raises(LearningCandidateError, match="authority boundary"):
            validate_learning_candidate(candidate)


def test_confirmed_bridge_claim_requires_local_evidence():
    candidate = _candidate()
    candidate["bridge_context"]["board"]["source_refs"] = []
    with pytest.raises(LearningCandidateError, match="confirmed board lacks evidence"):
        validate_learning_candidate(candidate)

    candidate = _candidate()
    candidate["bridge_context"]["board"]["source_refs"] = ["d" * 64]
    with pytest.raises(LearningCandidateError, match="outside candidate"):
        validate_learning_candidate(candidate)
