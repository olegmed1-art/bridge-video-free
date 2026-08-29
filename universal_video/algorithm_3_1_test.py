"""Versioned definition for the non-production Video Analysis 3.1-test line.

The definition composes the current 3.1 FREE methodology with the bounded
Universal Video runtime.  It is evidence-only: no output can be promoted to
the School Canon or replace the stable 3.1 FREE route automatically.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .speaker_structure import MIN_TEST_LABEL_COVERAGE

SCHEMA = "bridge-video-algorithm-definition-v1"
ALGORITHM_VERSION = "3.1-test"
ALGORITHM_REVISION = "3.1-test-r3"
BASE_ALGORITHM_VERSION = "3.1 FREE"
PROFILE_NAME = "bridge_lesson_3_1_test"
DEFINITION_FILE = "algorithm_3_1_test.json"
RESULT_SCOPE = "SHADOW_ONLY"
RELEASE_CHANNEL = "TEST"
_SAFE_REVISION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

BRIDGIT_LAYOUT_POLICY: dict[str, Any] = {
    "suit_order": ["H", "C", "D", "S"],
    "rank_order": list("AKQJT98765432"),
    "screen_axes": {
        "top": "LEFT_TO_RIGHT",
        "right": "TOP_TO_BOTTOM",
        "bottom": "LEFT_TO_RIGHT",
        "left": "TOP_TO_BOTTOM",
    },
    "allowed_rotations_clockwise": [0, 90, 180, 270],
    "seat_assignment": "SCREEN_POSITION_THEN_VERIFIED_ROTATION_TO_N_E_S_W",
    "evidence_class": "LAYOUT_SUGGESTION_ONLY",
}

BRIDGE_EVIDENCE_POLICY: dict[str, Any] = {
    "minimum_independent_frame_hashes_per_card": 2,
    "rank_and_suit_required": True,
    "independent_full_card_channel_required": True,
    "teacher_exact_card": "OBSERVATION_ONLY_AFTER_VERIFIED_ROLE_IDENTITY_AND_CONFIDENCE_GATES",
    "student_exact_card": "SUGGESTION_OR_CORROBORATION_ONLY",
    "board_number": "OBSERVED_WITH_INDEPENDENT_FRAME_CONSENSUS",
    "dealer_and_vulnerability": "DERIVED_FROM_CONFIRMED_DUPLICATE_BOARD_CYCLE_AND_CONFLICT_CHECKED",
    "fourth_hand": "DERIVED_ONLY_FROM_39_UNIQUE_OBSERVED_CARDS_IN_THREE_COMPLETE_HANDS",
    "verified_full_board": "REQUIRES_52_UNIQUE_OBSERVED_CARDS",
    "canonical_promotion_allowed": False,
}

SPEAKER_EVIDENCE_POLICY: dict[str, Any] = {
    "anonymous_labels_only": True,
    "minimum_segment_label_coverage": MIN_TEST_LABEL_COVERAGE,
    "minimum_speech_duration_label_coverage": MIN_TEST_LABEL_COVERAGE,
    "minimum_distinct_clusters": 2,
    "real_person_identity_claimed": False,
    "teacher_student_attribution": "SUGGESTION_ONLY",
}

CAPABILITIES: tuple[dict[str, Any], ...] = (
    {
        "id": "source_immutability",
        "state": "IMPLEMENTED",
        "modules": ["universal_video.contract", "universal_video.runner"],
        "boundary": "source is identified, bounded and never overwritten",
    },
    {
        "id": "runtime_attestation_v2",
        "state": "IMPLEMENTED",
        "modules": ["universal_video.resident_status", "universal_video.evidence_export"],
        "boundary": "installed and observed runtime commits must match",
    },
    {
        "id": "timestamped_asr_and_acoustic_qc",
        "state": "IMPLEMENTED",
        "modules": ["universal_video.runner"],
        "boundary": "empty, hallucinated or critically unreliable ASR fails closed",
    },
    {
        "id": "bridge_semantic_qc",
        "state": "DOWNSTREAM_3_1_COMPONENT",
        "modules": ["bridge_semantic_qc", "run_master_3_1_free_semantic_v2"],
        "boundary": "raw ASR is retained; unresolved critical corrections cannot become FACT",
    },
    {
        "id": "semantic_keyframes_and_timeline",
        "state": "IMPLEMENTED",
        "modules": ["universal_video.runner"],
        "boundary": "frames are hash-bound to the result manifest",
    },
    {
        "id": "anonymous_speaker_structure_v2",
        "state": "IMPLEMENTED_OPTIONAL",
        "modules": ["universal_video.speaker_structure", "bridge_speaker_diarization"],
        "boundary": "only SPEAKER_A..H; roles are suggestions; any doubt removes all labels",
    },
    {
        "id": "speaker_label_coverage_gate_v1",
        "state": "IMPLEMENTED_TEST_ONLY",
        "modules": ["universal_video.speaker_structure", "universal_video.result_conformance"],
        "boundary": "3.1-test requires at least 80 percent coverage by segments and speech duration",
    },
    {
        "id": "profiled_card_pixel_challenger_v2",
        "state": "SHADOW_CONTRACT_REQUIRES_APPROVED_BACKEND",
        "modules": ["bridge_vision.profiled_challenger", "tools.bridge_video_positions"],
        "boundary": "no approved pixel backend/profile means UNAVAILABLE, never an invented card",
    },
    {
        "id": "card_ocr_label_channel",
        "state": "SHADOW_COMPONENT",
        "modules": ["bridge_vision.ocr_card_labels"],
        "boundary": "rank and suit must both be complete and above confidence gates",
    },
    {
        "id": "verified_layout_and_rotation",
        "state": "SHADOW_COMPONENT",
        "modules": ["bridge_vision.profiled_challenger"],
        "boundary": "H-C-D-S ordering and 0/90/180/270 rotation are profile-bound suggestions",
    },
    {
        "id": "direct_speech_card_evidence",
        "state": "SHADOW_COMPONENT",
        "modules": ["bridge_vision.profiled_challenger"],
        "boundary": "verified teacher evidence may observe; student speech can only suggest/corroborate",
    },
    {
        "id": "board_metadata",
        "state": "SHADOW_COMPONENT",
        "modules": ["bridge_vision.profiled_challenger"],
        "boundary": "board number needs independent-frame consensus; dealer/vulnerability follow duplicate cycles",
    },
    {
        "id": "deal_reconstruction_39_to_13",
        "state": "SHADOW_COMPONENT",
        "modules": ["bridge_vision.canonical", "bridge_vision.profiled_challenger"],
        "boundary": "only an exact conflict-free 39-card observation may derive the fourth hand",
    },
    {
        "id": "dds3_optional",
        "state": "DEFERRED_OPTIONAL",
        "modules": ["universal_video.profiles"],
        "boundary": "solver output is separate evidence and never repairs uncertain visual input",
    },
    {
        "id": "pedagogical_master_analysis",
        "state": "DOWNSTREAM_3_1_COMPONENT",
        "modules": ["bridge_worker_3_1_free", "run_master_3_1_free_semantic_v2"],
        "boundary": "FACT, INFERENCE, RECOMMENDATION and UNCERTAIN remain distinct",
    },
    {
        "id": "result_conformance_and_evidence_export",
        "state": "IMPLEMENTED",
        "modules": ["universal_video.result_conformance", "universal_video.evidence_export"],
        "boundary": "hashes, provenance, runtime binding and deferred stages are independently rechecked",
    },
    {
        "id": "separated_readiness_matrix_v1",
        "state": "IMPLEMENTED",
        "modules": ["universal_video.readiness", "universal_video.result_conformance"],
        "boundary": "technical completion never implies domain, pedagogical or publication readiness",
    },
    {
        "id": "post_run_audit_loop",
        "state": "OPERATING_PROTOCOL",
        "modules": ["VIDEO_ANALYSIS_3_1_TEST.md"],
        "boundary": "one video, audit, bounded improvement, regression, then explicit next-video decision",
    },
)

MANDATORY_GATES = (
    "source_identity_bound",
    "runtime_commit_attested",
    "asr_qc_pass",
    "speaker_privacy_pass_or_inconclusive",
    "speaker_label_coverage_gate_pass_or_inconclusive",
    "card_output_shadow_only",
    "no_duplicate_card_conflict",
    "no_false_complete_deal",
    "artifact_hashes_verified",
    "executed_and_deferred_stages_disjoint",
    "readiness_matrix_verified",
    "canonical_promotion_disabled",
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_definition(*, source_revision: str = "development") -> dict[str, Any]:
    revision = str(source_revision or "").strip()
    if not _SAFE_REVISION.fullmatch(revision):
        raise ValueError("invalid source revision")
    return {
        "schema": SCHEMA,
        "algorithm_version": ALGORITHM_VERSION,
        "algorithm_revision": ALGORITHM_REVISION,
        "base_algorithm_version": BASE_ALGORITHM_VERSION,
        "profile": PROFILE_NAME,
        "release_channel": RELEASE_CHANNEL,
        "result_scope": RESULT_SCOPE,
        "canonical_promotion_allowed": False,
        "source_revision": revision,
        "capabilities": [dict(item) for item in CAPABILITIES],
        "mandatory_gates": list(MANDATORY_GATES),
        "production_activation_allowed": False,
        "next_video_auto_start_allowed": False,
        "bridgit_layout_policy": deepcopy(BRIDGIT_LAYOUT_POLICY),
        "bridge_evidence_policy": deepcopy(BRIDGE_EVIDENCE_POLICY),
        "speaker_evidence_policy": deepcopy(SPEAKER_EVIDENCE_POLICY),
    }


def definition_sha256(definition: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(definition)).hexdigest()


def write_definition(path: Path, *, source_revision: str) -> tuple[dict[str, Any], str]:
    definition = build_definition(source_revision=source_revision)
    path.write_text(json.dumps(definition, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return definition, definition_sha256(definition)


__all__ = [
    "ALGORITHM_REVISION",
    "ALGORITHM_VERSION",
    "BASE_ALGORITHM_VERSION",
    "BRIDGE_EVIDENCE_POLICY",
    "BRIDGIT_LAYOUT_POLICY",
    "CAPABILITIES",
    "DEFINITION_FILE",
    "MANDATORY_GATES",
    "PROFILE_NAME",
    "RELEASE_CHANNEL",
    "RESULT_SCOPE",
    "SPEAKER_EVIDENCE_POLICY",
    "SCHEMA",
    "build_definition",
    "definition_sha256",
    "write_definition",
]
