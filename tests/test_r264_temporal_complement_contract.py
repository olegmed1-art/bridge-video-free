import json
import sys
from types import SimpleNamespace
from pathlib import Path

import bridge_vision.bridgit_primary_video as primary_video
import bridge_vision.bridgit_rank_layout as rank_layout


def test_r264_contract_is_observed_only_until_real_validation():
    contract = json.loads(
        Path("ops/r264-recognizer-contract.json").read_text(encoding="utf-8")
    )
    assert contract["revision"] == "3.1-free-r26.4"
    assert contract["production_allowed"] is False
    assert contract["active_production_revision"] == "3.1-free-r26.3"
    assert contract["canonical_promotion_allowed"] is False
    assert contract["active_compute_route"] == "github_actions_legacy"


def test_r264_requires_strict_three_hand_shape_and_explicit_provenance():
    contract = json.loads(
        Path("ops/r264-recognizer-contract.json").read_text(encoding="utf-8")
    )
    derivation = contract["fourth_hand_derivation"]
    assert derivation["observed_cards_required"] == 39
    assert derivation["observed_cards_per_visible_seat"] == 13
    assert derivation["partial_fourth_hand_completion_allowed"] is False
    assert derivation["provenance"] == "INFERRED_DECK_COMPLEMENT"


def test_r263_defaults_remain_fail_closed_after_r264_code_lands():
    assert primary_video.ALLOW_FOURTH_HAND_DERIVATION is False
    assert rank_layout.TEMPORAL_CARD_UNION_ENABLED is False
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(
        encoding="utf-8"
    )
    assert 'BRIDGE_REQUESTED_ALGORITHM_REVISION: "3.1-free-r26.3"' in workflow
    assert "inputs.algorithm_revision == '3.1-free-r26.4'" in workflow


def test_r264_runtime_enables_both_new_gates():
    source = Path("bridge_runtime_hardening_r26_4.py").read_text(encoding="utf-8")
    assert 'REVISION = "3.1-free-r26.4"' in source
    assert "primary_video.ALLOW_FOURTH_HAND_DERIVATION = True" in source
    assert "rank_layout.TEMPORAL_CARD_UNION_ENABLED = True" in source


def test_version_identity_changes_only_when_both_r264_gates_are_enabled(monkeypatch):
    assert primary_video._runtime_version() == primary_video.PRIMARY_VIDEO_VERSION
    monkeypatch.setattr(primary_video, "ALLOW_FOURTH_HAND_DERIVATION", True)
    assert primary_video._runtime_version() == primary_video.PRIMARY_VIDEO_VERSION
    monkeypatch.setattr(rank_layout, "TEMPORAL_CARD_UNION_ENABLED", True)
    assert (
        primary_video._runtime_version()
        == primary_video.PRIMARY_VIDEO_TEMPORAL_COMPLEMENT_VERSION
    )


def test_generic_runtime_router_selects_r264_only_when_explicitly_requested(
    monkeypatch,
):
    import run_drive_3_1_free_generic as generic

    r263 = SimpleNamespace(name="r263")
    r264 = SimpleNamespace(name="r264")
    monkeypatch.setitem(sys.modules, "bridge_runtime_hardening_r26", r263)
    monkeypatch.setitem(sys.modules, "bridge_runtime_hardening_r26_4", r264)
    monkeypatch.delenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", raising=False)
    assert generic._hardening() is r263
    monkeypatch.setenv("BRIDGE_REQUESTED_ALGORITHM_REVISION", "3.1-free-r26.4")
    assert generic._hardening() is r264
