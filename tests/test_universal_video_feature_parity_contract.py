import copy
import json
from pathlib import Path

import pytest

from ops.validate_universal_video_feature_parity import (
    FeatureParityError,
    REQUIRED_CAPABILITIES,
    load_and_validate_feature_parity,
)
from ops.validate_universal_video_runtime_routing import RoutingContractError, load_and_validate


def _write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, payload: dict) -> None:
    parity_file = tmp_path / "parity.json"
    parity_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("ops.validate_universal_video_feature_parity.PARITY_FILE", parity_file)


def test_subject_parity_matrix_is_complete_and_honestly_blocked() -> None:
    parity = load_and_validate_feature_parity()
    assert set(parity["capabilities"]) == REQUIRED_CAPABILITIES
    assert parity["overall_status"] == "BLOCKED"
    assert all(item["production_parity_proven"] is False for item in parity["capabilities"].values())
    assert parity["capabilities"]["card_recognition"]["real_video_status"] == "FAIL"
    assert parity["capabilities"]["frame_extraction"]["real_video_status"] == "FAIL"
    assert parity["capabilities"]["terminal_receipt"]["real_video_status"] == "INCONCLUSIVE"


def test_unit_tests_cannot_create_parity_proven(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parity = copy.deepcopy(load_and_validate_feature_parity())
    item = parity["capabilities"]["card_recognition"]
    item.update(state="PARITY_PROVEN", production_parity_proven=True, blocker=None)
    _write(monkeypatch, tmp_path, parity)
    with pytest.raises(FeatureParityError, match="UV_FEATURE_PARITY_NOT_REAL_RUNTIME_PROVEN"):
        load_and_validate_feature_parity()


def test_forged_top_level_pass_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parity = copy.deepcopy(load_and_validate_feature_parity())
    parity["overall_status"] = "PASS"
    _write(monkeypatch, tmp_path, parity)
    with pytest.raises(FeatureParityError, match="UV_FEATURE_PARITY_STATUS_INCONSISTENT"):
        load_and_validate_feature_parity()


def test_missing_capability_cannot_disappear_from_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    parity = copy.deepcopy(load_and_validate_feature_parity())
    del parity["capabilities"]["auction_extraction"]
    _write(monkeypatch, tmp_path, parity)
    with pytest.raises(FeatureParityError, match="UV_FEATURE_PARITY_CAPABILITY_SET_INVALID"):
        load_and_validate_feature_parity()


def test_oracle_route_stays_blocked_by_subject_parity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    routing = copy.deepcopy(load_and_validate())
    routing["active_production_route"] = "oracle_container"
    routing["routes"]["github_actions_legacy"]["production_default"] = False
    routing["routes"]["oracle_container"]["production_default"] = True
    routing_file = tmp_path / "routing.json"
    routing_file.write_text(json.dumps(routing), encoding="utf-8")
    monkeypatch.setattr("ops.validate_universal_video_runtime_routing.ROUTING_FILE", routing_file)
    with pytest.raises(RoutingContractError, match="UV_RUNTIME_ROUTE_FEATURE_PARITY_BLOCKED"):
        load_and_validate()
