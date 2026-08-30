#!/usr/bin/env python3
"""Validate subject-matter parity from Video 3.1 FREE to the Oracle route."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARITY_FILE = ROOT / "ops/universal-video-r25-16-feature-parity.json"
EXPECTED_SCHEMA = "universal-video-subject-parity-v2"
EXPECTED_REVISION = "3.1-free-r25.16"
EXPECTED_TARGET = "oracle_container"
REQUIRED_CAPABILITIES = {
    "source_identity", "offline_asr", "asr_quality_control", "diarization",
    "named_speaker", "role_attribution", "frame_extraction", "card_recognition",
    "seat_geometry", "board_dealer_vulnerability", "auction_extraction",
    "deal_validation", "pbn", "bridge_semantics", "methodology_analysis",
    "learning_episodes", "drive_artifacts", "terminal_receipt", "idempotent_repeat",
}
ALLOWED_STATES = {
    "PARITY_PROVEN", "IMPLEMENTED_NOT_PROVEN", "PARTIAL", "MISSING", "NOT_APPLICABLE",
}
REAL_STATUSES = {"PASS", "FAIL", "INCONCLUSIVE", "UNAVAILABLE", "NOT_APPLICABLE"}
RUNTIME_STATUSES = {"WIRED_SHADOW", "COMPONENT_ONLY", "MISSING", "NOT_APPLICABLE"}
BLOCKER_RE = re.compile(r"^UV_PARITY_[A-Z0-9_]{1,96}$")
REAL_REF_RE = re.compile(r"^(issue|pr|run|docs):[^\s]{1,240}$")


class FeatureParityError(RuntimeError):
    pass


def _repository_file(value: object) -> Path:
    if not isinstance(value, str) or not value or value.startswith("/"):
        raise FeatureParityError("UV_FEATURE_PARITY_EVIDENCE_INVALID")
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise FeatureParityError("UV_FEATURE_PARITY_EVIDENCE_INVALID") from exc
    if not path.is_file():
        raise FeatureParityError("UV_FEATURE_PARITY_EVIDENCE_MISSING")
    return path


def _proof_receipt(value: object, capability: str) -> Path:
    path = _repository_file(value)
    relative = path.relative_to(ROOT.resolve())
    if relative.parent != Path("ops/universal-video-r25-16-parity-evidence") or relative.suffix != ".json":
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_PATH_INVALID")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_INVALID") from exc
    expected = {
        "schema": "universal-video-feature-parity-proof-v1",
        "capability": capability,
        "source_revision": EXPECTED_REVISION,
        "target_route": EXPECTED_TARGET,
        "status": "PASS",
        "independent_review": "PASS",
    }
    if any(receipt.get(key) != expected_value for key, expected_value in expected.items()):
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_INVALID")
    if receipt.get("evidence_class") not in {"REAL_VIDEO_HOLDOUT", "REAL_VIDEO_CANARY"}:
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_NOT_REAL_VIDEO")
    if not re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("algorithm_commit") or "")):
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_COMMIT_INVALID")
    refs = receipt.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(item, str) or not item for item in refs):
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_REFS_INVALID")
    return path


def load_and_validate_feature_parity() -> dict[str, object]:
    try:
        parity = json.loads(PARITY_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeatureParityError("UV_FEATURE_PARITY_UNREADABLE") from exc
    if parity.get("schema") != EXPECTED_SCHEMA:
        raise FeatureParityError("UV_FEATURE_PARITY_SCHEMA_INVALID")
    if parity.get("source_revision") != EXPECTED_REVISION:
        raise FeatureParityError("UV_FEATURE_PARITY_REVISION_INVALID")
    if parity.get("target_route") != EXPECTED_TARGET:
        raise FeatureParityError("UV_FEATURE_PARITY_TARGET_INVALID")
    capabilities = parity.get("capabilities")
    if not isinstance(capabilities, dict) or set(capabilities) != REQUIRED_CAPABILITIES:
        raise FeatureParityError("UV_FEATURE_PARITY_CAPABILITY_SET_INVALID")

    complete = True
    for name, capability in capabilities.items():
        if not isinstance(capability, dict):
            raise FeatureParityError("UV_FEATURE_PARITY_CAPABILITY_INVALID")
        state = capability.get("state")
        implemented = capability.get("implemented_in_code")
        tested = capability.get("covered_by_tests")
        real_status = capability.get("real_video_status")
        runtime_status = capability.get("runtime_status")
        production_proven = capability.get("production_parity_proven")
        legacy = capability.get("legacy_evidence")
        oracle = capability.get("oracle_evidence")
        test_evidence = capability.get("test_evidence")
        real_evidence = capability.get("real_evidence")
        proof = capability.get("proof")
        blocker = capability.get("blocker")
        if state not in ALLOWED_STATES or not isinstance(implemented, bool) or not isinstance(tested, bool):
            raise FeatureParityError("UV_FEATURE_PARITY_STATE_INVALID")
        if real_status not in REAL_STATUSES or runtime_status not in RUNTIME_STATUSES:
            raise FeatureParityError("UV_FEATURE_PARITY_RUNTIME_STATE_INVALID")
        if not isinstance(production_proven, bool):
            raise FeatureParityError("UV_FEATURE_PARITY_PRODUCTION_FLAG_INVALID")
        if not isinstance(legacy, list) or not legacy:
            raise FeatureParityError("UV_FEATURE_PARITY_LEGACY_EVIDENCE_INVALID")
        if not all(isinstance(value, list) for value in (oracle, test_evidence, real_evidence, proof)):
            raise FeatureParityError("UV_FEATURE_PARITY_EVIDENCE_LIST_INVALID")
        for value in [*legacy, *oracle, *test_evidence]:
            _repository_file(value)
        if any(not isinstance(value, str) or not REAL_REF_RE.fullmatch(value) for value in real_evidence):
            raise FeatureParityError("UV_FEATURE_PARITY_REAL_EVIDENCE_INVALID")
        for value in proof:
            _proof_receipt(value, name)

        if state == "PARITY_PROVEN":
            if not all((implemented, tested, production_proven)):
                raise FeatureParityError("UV_FEATURE_PARITY_PROOF_INCOMPLETE")
            if real_status != "PASS" or runtime_status != "WIRED_SHADOW":
                raise FeatureParityError("UV_FEATURE_PARITY_NOT_REAL_RUNTIME_PROVEN")
            if not oracle or not test_evidence or not real_evidence or not proof or blocker is not None:
                raise FeatureParityError("UV_FEATURE_PARITY_PROOF_INCOMPLETE")
        elif state == "NOT_APPLICABLE":
            if runtime_status != "NOT_APPLICABLE" or real_status != "NOT_APPLICABLE" or blocker is not None:
                raise FeatureParityError("UV_FEATURE_PARITY_NOT_APPLICABLE_INVALID")
        else:
            complete = False
            if production_proven or not isinstance(blocker, str) or not BLOCKER_RE.fullmatch(blocker):
                raise FeatureParityError("UV_FEATURE_PARITY_BLOCKER_INVALID")
            if state == "MISSING" and (implemented or tested or runtime_status != "MISSING"):
                raise FeatureParityError("UV_FEATURE_PARITY_MISSING_STATE_INVALID")
            if state in {"IMPLEMENTED_NOT_PROVEN", "PARTIAL"} and not implemented:
                raise FeatureParityError("UV_FEATURE_PARITY_IMPLEMENTATION_STATE_INVALID")

    expected_status = "PASS" if complete else "BLOCKED"
    if parity.get("overall_status") != expected_status:
        raise FeatureParityError("UV_FEATURE_PARITY_STATUS_INCONSISTENT")
    return parity


def require_feature_parity_pass() -> None:
    parity = load_and_validate_feature_parity()
    if parity["overall_status"] != "PASS":
        raise FeatureParityError("UV_RUNTIME_ROUTE_FEATURE_PARITY_BLOCKED")


if __name__ == "__main__":
    try:
        result = load_and_validate_feature_parity()
    except FeatureParityError as exc:
        print(str(exc))
        raise SystemExit(78)
    print(f"UNIVERSAL_VIDEO_FEATURE_PARITY_VALID status={result['overall_status']}")
