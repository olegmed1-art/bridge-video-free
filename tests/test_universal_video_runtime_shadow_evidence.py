import hashlib
import json
from pathlib import Path

import pytest

from bridge_vision.profiled_challenger import (
    CARDS,
    ProfiledCardChallenger,
    build_teach_profile,
    parse_profile,
)
from tools.bridge_video_positions import process_job_frames
from universal_video.runtime_shadow_evidence import (
    BACKEND_AUTHORITY,
    PROFILE_AUTHORITY,
    RECEIPT_FILE,
    SCHEMA,
    SHADOW_OUTPUT_FILE,
    unavailable_receipt,
    validate_receipt,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def profile():
    raw = build_teach_profile(
        profile_id="runtime-test-v1",
        reference_frame_sha256="a" * 64,
        reference_size={"width": 1000, "height": 1000},
        table_region={"x": 0, "y": 0, "w": 1000, "h": 1000},
        rank_templates={rank: digest("rank:" + rank) for rank in "AKQJT98765432"},
        suit_templates={suit: digest("suit:" + suit) for suit in "SHDC"},
        card_templates={card: digest("card:" + card) for card in CARDS},
        rank_suit_channel_id="rank-suit-test-v1",
        reference_channel_id="reference-test-v1",
        human_verified=True,
        verification={
            "method": "HUMAN_LABEL_REVIEW",
            "reviewer_id": "test-reviewer",
            "verified_at": "2026-08-29T00:00:00Z",
            "reference_frame_sha256": "a" * 64,
        },
        ordering_prior={
            "human_verified": True,
            "suit_order": ["H", "C", "D", "S"],
            "rank_order": list("AKQJT98765432"),
            "seat_axes": {"N": "X_ASC", "E": "Y_ASC", "S": "X_ASC", "W": "Y_ASC"},
            "seat_positions": {"top": "N", "right": "E", "bottom": "S", "left": "W"},
        },
        gates={
            "min_registration_inliers": 20,
            "min_registration_inlier_ratio": 0.8,
            "min_deal_match_inliers": 20,
            "min_deal_match_inlier_ratio": 0.8,
            "min_rank_confidence": 0.9,
            "min_suit_confidence": 0.9,
            "min_reference_confidence": 0.9,
            "min_card_confidence": 0.9,
            "min_ambiguous_candidate_confidence": 0.7,
            "min_temporal_observations": 2,
            "seat_dead_zone": 0.08,
        },
    )
    return parse_profile(raw)


def profile_hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value.recognizer_view(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def prepare_job(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    first = frames / "first.jpg"
    second = frames / "second.jpg"
    first.write_bytes(b"first-runtime-frame")
    second.write_bytes(b"second-runtime-frame")
    frame_rows = [
        {"file": path.name, "time": float(index * 30), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for index, path in enumerate((first, second), start=1)
    ]
    (tmp_path / "manifest.json").write_text(
        json.dumps({"job_id": "runtime-shadow-test", "source_fingerprint": "source-test", "frames": frame_rows}),
        encoding="utf-8",
    )
    return first, second


def backend_payload(frame: Path, _profile):
    frame_hash = hashlib.sha256(frame.read_bytes()).hexdigest()
    return {
        "frame_sha256": frame_hash,
        "registration": {
            "reference_frame_sha256": "a" * 64,
            "inliers": 100,
            "inlier_ratio": 0.99,
            "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        },
        "deal_identity": {"kind": "EXPLICIT_BOARD", "scope": "test", "value": "board-1"},
        "cards": [
            {
                "box": {"x": 490, "y": 50, "w": 20, "h": 20},
                "rank": {"value": "A", "confidence": 0.99, "channel_id": "rank-suit-test-v1"},
                "suit": {"value": "S", "confidence": 0.99, "channel_id": "rank-suit-test-v1"},
                "reference_match": {
                    "card": "AS",
                    "confidence": 0.99,
                    "channel_id": "reference-test-v1",
                },
            }
        ],
    }


def challenger():
    return ProfiledCardChallenger(
        profile(),
        backend_payload,
        backend_id="pixel-test-v1",
        backend_sha256="b" * 64,
    )


def context(detector):
    return {
        "schema": SCHEMA,
        "request_commit": "1" * 40,
        "requested_runtime_commit": "2" * 40,
        "installed_runtime_commit": "2" * 40,
        "observed_job_runtime_commit": "2" * 40,
        "profile_id": detector.profile.profile_id,
        "profile_hash": profile_hash(detector.profile),
        "profile_authority": PROFILE_AUTHORITY,
        "profile_authority_sha256": detector.profile.verification_sha256,
        "backend_id": detector.backend_id,
        "backend_hash": detector.backend_sha256,
        "backend_authority": BACKEND_AUTHORITY,
    }


def read_receipt(root: Path):
    return json.loads((root / RECEIPT_FILE).read_text(encoding="utf-8"))


def test_profiled_shadow_requires_explicit_runtime_context_before_invocation(tmp_path: Path):
    prepare_job(tmp_path)
    calls = []
    detector = ProfiledCardChallenger(
        profile(),
        lambda *args: calls.append(args),
        backend_id="pixel-test-v1",
        backend_sha256="b" * 64,
    )

    with pytest.raises(ValueError, match="ACTIVATION_CONTEXT_MISSING"):
        process_job_frames(tmp_path, profiled_challenger=detector)

    assert calls == []
    assert not (tmp_path / SHADOW_OUTPUT_FILE).exists()
    receipt = read_receipt(tmp_path)
    assert receipt["state"] == "UNAVAILABLE"
    assert receipt["unavailable_reasons"] == ["ACTIVATION_CONTEXT_MISSING"]
    assert receipt["challenger_invoked"] is False
    assert receipt["canonical_promotion_allowed"] is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("installed_runtime_commit", "3" * 40, "RUNTIME_COMMIT_MISMATCH"),
        ("profile_authority", "SELF_ATTESTED", "PROFILE_AUTHORITY_MISSING"),
        ("profile_authority_sha256", "c" * 64, "PROFILE_AUTHORITY_MISMATCH"),
        ("backend_authority", "UNAPPROVED", "BACKEND_AUTHORITY_MISSING"),
        ("profile_hash", "d" * 64, "PROFILE_HASH_MISMATCH"),
        ("backend_hash", "d" * 64, "BACKEND_ID_MISMATCH"),
    ],
)
def test_activation_gate_fails_closed_with_bounded_reason(tmp_path: Path, field, value, reason):
    prepare_job(tmp_path)
    detector = challenger()
    runtime = context(detector)
    runtime[field] = value

    with pytest.raises(ValueError, match=reason):
        process_job_frames(
            tmp_path,
            profiled_challenger=detector,
            shadow_runtime_context=runtime,
        )

    receipt = read_receipt(tmp_path)
    assert receipt["state"] == "UNAVAILABLE"
    assert receipt["unavailable_reasons"] == [reason]
    assert not (tmp_path / SHADOW_OUTPUT_FILE).exists()


def test_unknown_activation_field_is_rejected(tmp_path: Path):
    prepare_job(tmp_path)
    detector = challenger()
    runtime = {**context(detector), "activate": True}
    with pytest.raises(ValueError, match="ACTIVATION_CONTEXT_UNKNOWN_FIELD"):
        process_job_frames(
            tmp_path,
            profiled_challenger=detector,
            shadow_runtime_context=runtime,
        )


def test_observed_receipt_binds_runtime_profile_backend_and_shadow_output(tmp_path: Path):
    prepare_job(tmp_path)
    canonical = tmp_path / "bridge_positions.jsonl"
    canonical.write_text('{"preserved":true}\n', encoding="utf-8")
    canonical_before = canonical.read_bytes()
    detector = challenger()
    runtime = context(detector)

    summary = process_job_frames(
        tmp_path,
        profiled_challenger=detector,
        shadow_runtime_context=runtime,
    )

    receipt = read_receipt(tmp_path)
    shadow = tmp_path / SHADOW_OUTPUT_FILE
    assert receipt["state"] == "OBSERVED"
    assert receipt["runtime_binding"] == "PASS"
    assert receipt["request_commit"] == "1" * 40
    assert receipt["requested_runtime_commit"] == "2" * 40
    assert receipt["installed_runtime_commit"] == "2" * 40
    assert receipt["observed_job_runtime_commit"] == "2" * 40
    assert receipt["profile_id"] == detector.profile.profile_id
    assert receipt["profile_hash"] == profile_hash(detector.profile)
    assert receipt["backend_id"] == "pixel-test-v1"
    assert receipt["backend_hash"] == "b" * 64
    assert receipt["challenger_invoked"] is True
    assert receipt["shadow_only"] is True
    assert receipt["shadow_output_locator"] == SHADOW_OUTPUT_FILE
    assert receipt["shadow_output_sha256"] == hashlib.sha256(shadow.read_bytes()).hexdigest()
    assert receipt["canonical_output_untouched"] is True
    assert receipt["canonical_promotion_allowed"] is False
    assert receipt["publication_state"] == "NOT_PUBLISHED"
    assert receipt["unavailable_reasons"] == []
    assert canonical.read_bytes() == canonical_before
    assert summary["runtime_evidence_receipt"] == RECEIPT_FILE
    assert summary["runtime_evidence_receipt_sha256"] == hashlib.sha256(
        (tmp_path / RECEIPT_FILE).read_bytes()
    ).hexdigest()


def test_context_without_challenger_is_observable_unavailable(tmp_path: Path):
    prepare_job(tmp_path)
    detector = challenger()
    with pytest.raises(ValueError, match="CHALLENGER_MISSING"):
        process_job_frames(tmp_path, shadow_runtime_context=context(detector))
    assert read_receipt(tmp_path)["unavailable_reasons"] == ["CHALLENGER_MISSING"]


def test_attestation_schema_is_versioned_closed_and_promotion_false():
    schema = json.loads(
        Path("ops/universal-video-runtime-shadow-attestation.schema.json").read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema"]["const"] == SCHEMA
    assert schema["properties"]["canonical_promotion_allowed"]["const"] is False
    assert schema["properties"]["publication_state"]["const"] == "NOT_PUBLISHED"
    assert schema["properties"]["unavailable_reasons"]["maxItems"] == 16
    assert set(schema["required"]) == set(
        validate_receipt(unavailable_receipt(None, ["ACTIVATION_CONTEXT_MISSING"]))
    )


def test_receipt_validator_rejects_promotion_and_commit_mismatch():
    missing = unavailable_receipt(None, ["ACTIVATION_CONTEXT_MISSING"])
    missing["canonical_promotion_allowed"] = True
    with pytest.raises(ValueError, match="safety boundary"):
        validate_receipt(missing)

    observed = {
        "schema": SCHEMA,
        "state": "OBSERVED",
        "request_commit": "1" * 40,
        "requested_runtime_commit": "2" * 40,
        "installed_runtime_commit": "3" * 40,
        "observed_job_runtime_commit": "2" * 40,
        "runtime_binding": "PASS",
        "profile_id": "profile-v1",
        "profile_hash": "4" * 64,
        "profile_authority": PROFILE_AUTHORITY,
        "profile_authority_sha256": "5" * 64,
        "backend_id": "backend-v1",
        "backend_hash": "6" * 64,
        "backend_authority": BACKEND_AUTHORITY,
        "challenger_invoked": True,
        "shadow_only": True,
        "shadow_output_locator": SHADOW_OUTPUT_FILE,
        "shadow_output_sha256": "7" * 64,
        "canonical_output_untouched": True,
        "canonical_promotion_allowed": False,
        "publication_state": "NOT_PUBLISHED",
        "unavailable_reasons": [],
    }
    with pytest.raises(ValueError, match="commit mismatch"):
        validate_receipt(observed)
