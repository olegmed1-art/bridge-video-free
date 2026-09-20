import json
from pathlib import Path


ACTIVE_RECOGNIZER_SOURCES = (
    "bridge_vision/bridgit_rank_layout.py",
    "bridge_vision/bridgit_gold_profile.py",
    "bridge_vision/gambler_reference_authority.py",
    "bridge_vision/bridgit_gambler_rank_layout.py",
    "bridge_vision/bridgit_primary_video.py",
    "bridge_vision/bridgit_primary_production.py",
)


def test_r263_production_promotion_receipt_is_fail_closed():
    validation = json.loads(
        Path("ops/r263-production-validation.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        Path("ops/r263-production-promotion.json").read_text(encoding="utf-8")
    )

    assert validation["revision"] == receipt["revision"] == "3.1-free-r26.3"
    assert validation["validation_state"] == "PASS"
    assert receipt["promotion_scope"] == "PRODUCTION_RUNTIME"
    assert receipt["promotion_state"] == "READY_FOR_PRODUCTION_PROMOTION"

    workflow = validation["workflow"]
    assert workflow["conclusion"] == workflow["process_outcome"] == "success"
    assert workflow["requested_revision"] == receipt["revision"]
    assert workflow["duplicate_heavy_runs"] == 0

    output = validation["output"]
    control = validation["control_deal"]
    assert output["ai_done_revision"] == receipt["revision"]
    assert output["primary_visual_deals"] == 1
    assert output["false_complete_deals"] == 0
    assert output["duplicate_complete_deals"] == 0
    assert control["recognized_cards"] == control["unique_cards"] == 52
    assert control["cards_per_seat"] == 13
    assert control["exact_reference_match"] is True
    assert control["no_uncertainty"] is True
    assert set(control["hands"]) == {"N", "E", "S", "W"}
    assert all(len(cards) == 13 for cards in control["hands"].values())
    assert len({card for cards in control["hands"].values() for card in cards}) == 52

    recognizer = validation["recognizer_evidence"]
    assert recognizer["profile_verification_method"] == "AUTONOMOUS_HASH_GEOMETRY_V1"
    assert recognizer["human_verification_required"] is False
    assert recognizer["sprite_binding"] == "PINNED_SHA256_ONLY"
    assert recognizer["backend_status"] == "SHADOW_FULL_LAYOUT_CANDIDATE"
    assert recognizer["primary_status"] == "VISUAL_PRIMARY_RECOGNIZED"

    contract = receipt["runtime_contract"]
    assert contract["recognizer_profile"] == "AUTONOMOUS_HASH_GEOMETRY_V1"
    assert contract["human_verification_required"] is False
    assert contract["derive_fourth_hand"] is False
    assert contract["deck_complement"] is False
    assert contract["hidden_hand_inference"] is False
    assert contract["unknown_hidden_cards_remain_unknown"] is True

    boundary = receipt["promotion_boundary"]
    assert boundary["canonical_promotion_allowed"] is False
    assert boundary["school_canon_write_allowed"] is False
    assert boundary["world_write_allowed"] is False
    assert boundary["independent_holdout_passed"] is False
    assert boundary["oracle_container_cutover_included"] is False
    assert boundary["active_compute_route"] == "github_actions_legacy"

    forbidden = (
        "human_verified",
        "human-approved",
        "human_approved",
        "human_label_review",
        "reviewer_id",
        "verified_at",
    )
    for filename in ACTIVE_RECOGNIZER_SOURCES:
        source = Path(filename).read_text(encoding="utf-8").lower()
        assert all(term not in source for term in forbidden), filename

    primary = Path("bridge_vision/bridgit_primary_production.py").read_text(
        encoding="utf-8"
    )
    assert "build_autonomous_gold_profile" in primary
    assert '"VISUAL_ONLY; NO_DECK_COMPLEMENT"' in primary
    assert '"canonical_promotion_allowed": False' in primary
