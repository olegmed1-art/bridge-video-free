import copy
import json
from pathlib import Path

import pytest

from ops.validate_universal_video_feature_parity import (
    FeatureParityError,
    load_and_validate_feature_parity,
)
from ops.validate_universal_video_runtime_routing import (
    RoutingContractError,
    load_and_validate,
)


def test_r25_16_to_oracle_parity_is_honestly_blocked() -> None:
    parity = load_and_validate_feature_parity()
    assert parity["source_revision"] == "3.1-free-r25.16"
    assert parity["target_route"] == "oracle_container"
    assert parity["overall_status"] == "BLOCKED"
    blocked = {
        name
        for name, capability in parity["capabilities"].items()
        if capability["state"] != "PARITY_PROVEN"
    }
    assert blocked == set(parity["capabilities"])
    assert {
        "named_speaker_identity_overlay",
        "bridge_semantic_and_methodology_analysis",
        "r25_16_deal_review_pdf",
        "longitudinal_learning_candidates",
        "neon_result_persistence",
    } <= blocked


def test_oracle_cannot_become_active_while_parity_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routing = copy.deepcopy(load_and_validate())
    routing["active_production_route"] = "oracle_container"
    routing["routes"]["github_actions_legacy"]["production_default"] = False
    routing["routes"]["oracle_container"]["production_default"] = True
    routing_file = tmp_path / "routing.json"
    routing_file.write_text(json.dumps(routing), encoding="utf-8")
    monkeypatch.setattr(
        "ops.validate_universal_video_runtime_routing.ROUTING_FILE",
        routing_file,
    )
    with pytest.raises(RoutingContractError, match="UV_RUNTIME_ROUTE_FEATURE_PARITY_BLOCKED"):
        load_and_validate()


def test_forged_pass_cannot_hide_unproven_capabilities(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parity = copy.deepcopy(load_and_validate_feature_parity())
    parity["overall_status"] = "PASS"
    parity_file = tmp_path / "parity.json"
    parity_file.write_text(json.dumps(parity), encoding="utf-8")
    monkeypatch.setattr(
        "ops.validate_universal_video_feature_parity.PARITY_FILE",
        parity_file,
    )
    with pytest.raises(FeatureParityError, match="UV_FEATURE_PARITY_STATUS_INCONSISTENT"):
        load_and_validate_feature_parity()


def test_source_file_cannot_be_used_as_parity_proof(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parity = copy.deepcopy(load_and_validate_feature_parity())
    capability = parity["capabilities"]["deterministic_source_identity"]
    capability["state"] = "PARITY_PROVEN"
    capability["proof"] = ["universal_video/runner.py"]
    capability["blocker"] = None
    parity_file = tmp_path / "parity.json"
    parity_file.write_text(json.dumps(parity), encoding="utf-8")
    monkeypatch.setattr(
        "ops.validate_universal_video_feature_parity.PARITY_FILE",
        parity_file,
    )
    with pytest.raises(FeatureParityError, match="UV_FEATURE_PARITY_PROOF_PATH_INVALID"):
        load_and_validate_feature_parity()
