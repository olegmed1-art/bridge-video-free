import json
from pathlib import Path


def test_r262_production_promotion_receipt_is_fail_closed():
    receipt = json.loads(Path("ops/r262-production-promotion.json").read_text(encoding="utf-8"))
    baseline = json.loads(Path("ops/r262-production-baseline.json").read_text(encoding="utf-8"))
    source = Path("bridge_vision/bridgit_primary_production.py").read_text(encoding="utf-8")

    assert receipt["revision"] == "3.1-free-r26.2"
    assert receipt["promotion_scope"] == "PRODUCTION_RUNTIME"
    assert receipt["promotion_state"] == "READY_FOR_PRODUCTION_PROMOTION"
    assert receipt["production_evidence"]["workflow_conclusion"] == "success"
    assert receipt["production_evidence"]["recognized_cards"] == 52
    assert receipt["production_evidence"]["unique_cards"] == 52
    assert receipt["production_evidence"]["cards_per_seat"] == 13
    assert receipt["production_evidence"]["exact_reference_match"] is True

    assert baseline["revision"] == receipt["revision"]
    assert baseline["control_deal"]["recognized_cards"] == 52
    assert baseline["control_deal"]["unique_cards"] == 52
    assert baseline["control_deal"]["exact_reference_match"] is True

    boundary = receipt["promotion_boundary"]
    assert boundary["canonical_promotion_allowed"] is False
    assert boundary["school_canon_write_allowed"] is False
    assert boundary["world_write_allowed"] is False
    assert boundary["oracle_container_cutover_included"] is False
    assert boundary["active_compute_route"] == "github_actions_legacy"

    contract = receipt["runtime_contract"]
    assert contract["derive_fourth_hand"] is False
    assert contract["deck_complement"] is False
    assert contract["hidden_hand_inference"] is False
    assert contract["unknown_hidden_cards_remain_unknown"] is True

    assert '"VISUAL_PRIMARY_RECOGNIZED"' in source
    assert '"VISUAL_ONLY; NO_DECK_COMPLEMENT"' in source
    assert '"canonical_promotion_allowed": False' in source
