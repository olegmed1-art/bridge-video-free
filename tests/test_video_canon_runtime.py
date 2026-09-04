from __future__ import annotations

from copy import deepcopy
import uuid

import pytest

from bridge_contracts.video_canon_runtime import (
    VideoCanonRuntimeError,
    build_promotion_delivery,
    extract_canon_candidates,
    verify_canon_candidate,
)
from tests.test_video_canon_ai_promotion import _bundle
from tests.test_video_canon_evidence import _assertion, _learning


def _video_result() -> dict:
    learning = _learning()
    assertion = _assertion()
    assertion["semantic_confidence"] = 0.98
    transcript = learning["transcript_evidence"][0]
    observation = {
        **assertion,
        "transcript": {
            **transcript,
            "text": assertion["statement"],
        },
        "frame_evidence": learning["frame_evidence"][0],
        "observed_episode": learning["observed_episode"],
        "bridge_context": {
            key: {**value, "status": "CONFIRMED", "value": value["value"] or "verified" ,
                  "source_refs": value["source_refs"] or [transcript["locator"]]}
            for key, value in learning["bridge_context"].items()
        },
        "preliminary_skill": learning["preliminary_skill"],
        "confidence": learning["confidence"],
        "system_profile": "natural-v1",
        "learner_level": "beginner-1",
    }
    return {
        "schema": "verified-video-canon-input-v1",
        "status": "VIDEO_ANALYSIS_VERIFIED",
        "job_id": "video-job-1",
        "source": {
            **learning["source"],
            "master_artifact_sha256": "d" * 64,
        },
        "algorithm_revision": "video31-r1/canon-extractor-v1",
        "observations": [observation],
    }


def _verdict(candidate: dict, level: str) -> dict:
    suffix = level.casefold()
    return {
        "schema": "video-canon-assurance-verdict-v1",
        "candidate_payload_hash": candidate["payload_hash"],
        "assurance_level": level,
        "verdict": "VERIFIED_FOR_PROMOTION",
        "verifier_family": f"independent-{suffix}",
        "verifier_version": "pinned-v1",
        "execution_principal": f"svc-{suffix}",
        "evidence_sha256": ("2" if level == "I2" else "3") * 64,
        "canon_snapshot_sha256": "c" * 64,
        "system_profile": "natural-v1",
        "learner_level": "beginner-1",
        "provenance_verified": True,
        "hidden_information_clear": True,
        "profile_unambiguous": True,
        "canon_conflict": False,
        "deterministic": True,
    }


def test_full_synthetic_chain_reaches_fenced_atomic_delivery_without_writing_canon():
    extracted = extract_canon_candidates(_video_result())
    assert extracted["status"] == "EXTRACTED"
    assert extracted["authoritative_write_performed"] is False
    candidate = extracted["candidates"][0]
    assert candidate["payload"]["authority_class"] == "TEACHER_VIDEO"
    assert candidate["payload"]["system_profile"] == "natural-v1"
    assert candidate["payload"]["learner_level"] == "beginner-1"

    verified = verify_canon_candidate(
        candidate,
        _bundle(candidate),
        [_verdict(candidate, "I2"), _verdict(candidate, "I3")],
    )
    assert verified["status"] == "VERIFIED_I2_I3"
    assert verified["authoritative_write_performed"] is False

    delivery = build_promotion_delivery(
        verified,
        delivery_id=uuid.uuid4(),
        rule_id=uuid.uuid4(),
        lease_owner="canon-consumer-1",
        lease_token=uuid.uuid4(),
        fencing_token=7,
    )
    assert delivery["operation"] == "ATOMIC_PROMOTION"
    assert delivery["post_write_integrity_required"] is True
    assert delivery["rollback_on_any_error"] is True
    assert len(delivery["delivery_sha256"]) == 64


def test_extractor_rejects_unverified_video_and_does_not_invent_candidates():
    video = _video_result()
    video["status"] = "ANALYSIS_DONE"
    with pytest.raises(VideoCanonRuntimeError, match="not verified terminal"):
        extract_canon_candidates(video)

    video = _video_result()
    del video["observations"][0]["tests"]
    extracted = extract_canon_candidates(video)
    assert extracted["status"] == "NO_CANDIDATE_EXTRACTED"
    assert extracted["gaps"][0]["status"] == "NEEDS_EVIDENCE"


@pytest.mark.parametrize("mutation, expected", [
    (lambda verdicts: verdicts.pop(), "I2 and I3"),
    (lambda verdicts: verdicts[1].update(verifier_family="independent-i2"), "must be independent"),
    (lambda verdicts: verdicts[1].update(candidate_payload_hash="9" * 64), "another candidate"),
    (lambda verdicts: verdicts[1].update(canon_snapshot_sha256="9" * 64), "different Canon snapshots"),
])
def test_verification_fails_closed_on_missing_independence_or_changed_identity(mutation, expected):
    candidate = extract_canon_candidates(_video_result())["candidates"][0]
    verdicts = [_verdict(candidate, "I2"), _verdict(candidate, "I3")]
    mutation(verdicts)
    with pytest.raises(VideoCanonRuntimeError, match=expected):
        verify_canon_candidate(candidate, _bundle(candidate), verdicts)


@pytest.mark.parametrize("status", [
    "REJECTED", "NEEDS_EVIDENCE", "CANON_CONFLICT", "PROFILE_AMBIGUITY",
])
def test_non_pass_verdict_never_creates_promotion(status):
    candidate = extract_canon_candidates(_video_result())["candidates"][0]
    verdicts = [_verdict(candidate, "I2"), _verdict(candidate, "I3")]
    verdicts[1]["verdict"] = status
    result = verify_canon_candidate(candidate, _bundle(candidate), verdicts)
    assert result["status"] == status
    assert result["promotion"] is None


def test_delivery_rejects_stale_fencing_token_and_mutated_verification():
    candidate = extract_canon_candidates(_video_result())["candidates"][0]
    verified = verify_canon_candidate(
        candidate, _bundle(candidate), [_verdict(candidate, "I2"), _verdict(candidate, "I3")]
    )
    with pytest.raises(VideoCanonRuntimeError, match="fencing token"):
        build_promotion_delivery(
            verified, delivery_id=uuid.uuid4(), rule_id=uuid.uuid4(),
            lease_owner="worker", lease_token=uuid.uuid4(), fencing_token=0,
        )
    changed = deepcopy(verified)
    changed["verification_bundle_sha256"] = "8" * 64
    with pytest.raises(VideoCanonRuntimeError, match="hash mismatch"):
        build_promotion_delivery(
            changed, delivery_id=uuid.uuid4(), rule_id=uuid.uuid4(),
            lease_owner="worker", lease_token=uuid.uuid4(), fencing_token=1,
        )
