#!/usr/bin/env python3
"""Validate the evidence-bound r25.16 to Oracle feature-parity contract."""

from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARITY_FILE = ROOT / "ops/universal-video-r25-16-feature-parity.json"
EXPECTED_SCHEMA = "universal-video-feature-parity-v1"
EXPECTED_REVISION = "3.1-free-r25.16"
EXPECTED_TARGET = "oracle_container"
ALLOWED_STATES = {
    "PARITY_PROVEN",
    "IMPLEMENTED_NOT_PARITY_PROVEN",
    "COMPONENT_ONLY",
    "MISSING",
}
BLOCKER_RE = re.compile(r"^UV_PARITY_[A-Z0-9_]{1,80}$")


class FeatureParityError(RuntimeError):
    pass


def _evidence_path(value: object) -> Path:
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
    path = _evidence_path(value)
    relative = path.relative_to(ROOT.resolve())
    if (
        relative.parent != Path("ops/universal-video-r25-16-parity-evidence")
        or relative.suffix != ".json"
    ):
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
    }
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise FeatureParityError("UV_FEATURE_PARITY_PROOF_INVALID")
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
    if not isinstance(capabilities, dict) or not capabilities:
        raise FeatureParityError("UV_FEATURE_PARITY_CAPABILITIES_INVALID")
    all_proven = True
    for name, capability in capabilities.items():
        if not isinstance(name, str) or not isinstance(capability, dict):
            raise FeatureParityError("UV_FEATURE_PARITY_CAPABILITY_INVALID")
        state = capability.get("state")
        legacy = capability.get("legacy_evidence")
        oracle = capability.get("oracle_evidence")
        proof = capability.get("proof")
        blocker = capability.get("blocker")
        if state not in ALLOWED_STATES:
            raise FeatureParityError("UV_FEATURE_PARITY_STATE_INVALID")
        if not isinstance(legacy, list) or not legacy:
            raise FeatureParityError("UV_FEATURE_PARITY_LEGACY_EVIDENCE_INVALID")
        if not isinstance(oracle, list) or not isinstance(proof, list):
            raise FeatureParityError("UV_FEATURE_PARITY_ORACLE_EVIDENCE_INVALID")
        for value in [*legacy, *oracle]:
            _evidence_path(value)
        for value in proof:
            _proof_receipt(value, name)
        if state == "PARITY_PROVEN":
            if not oracle or not proof or blocker is not None:
                raise FeatureParityError("UV_FEATURE_PARITY_PROOF_INCOMPLETE")
        else:
            all_proven = False
            if not isinstance(blocker, str) or not BLOCKER_RE.fullmatch(blocker):
                raise FeatureParityError("UV_FEATURE_PARITY_BLOCKER_INVALID")

    expected_status = "PASS" if all_proven else "BLOCKED"
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
