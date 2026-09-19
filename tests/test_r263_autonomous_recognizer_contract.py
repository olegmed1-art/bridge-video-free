from pathlib import Path

import bridge_runtime_hardening_r26 as runtime
from bridge_vision import bridgit_rank_layout as rank_layout
from bridge_vision.gambler_reference_authority import (
    PINNED_GAMBLER_CLASSIC_SPRITE_SHA256,
)


ACTIVE_RECOGNIZER_SOURCES = (
    "bridge_vision/bridgit_rank_layout.py",
    "bridge_vision/bridgit_gold_profile.py",
    "bridge_vision/gambler_reference_authority.py",
    "bridge_vision/bridgit_gambler_rank_layout.py",
    "bridge_vision/bridgit_primary_video.py",
    "bridge_vision/bridgit_primary_production.py",
)


def test_r263_runtime_has_no_human_verification_prerequisite() -> None:
    forbidden = (
        "human_verified",
        "human-approved",
        "human_approved",
        "human_label_review",
        "reviewer_id",
        "verified_at",
        "approved_by_operator",
        "trusted_by_owner",
    )
    for filename in ACTIVE_RECOGNIZER_SOURCES:
        source = Path(filename).read_text(encoding="utf-8").lower()
        assert all(term not in source for term in forbidden), filename


def test_r263_machine_authorities_are_exact_and_complete() -> None:
    assert runtime.REVISION == "3.1-free-r26.3"
    assert len(rank_layout.AUTONOMOUS_MANIFEST_SHA256) == 64
    assert len(rank_layout.AUTONOMOUS_VALIDATION_SHA256) == 64
    assert len(rank_layout.AUTONOMOUS_INTEGRITY_SHA256) == 64
    assert len(rank_layout.AUTONOMOUS_TEMPLATE_SET_SHA256) == 64
    assert len(rank_layout.AUTONOMOUS_RANK_SET_SHA256) == 64
    assert set(PINNED_GAMBLER_CLASSIC_SPRITE_SHA256) == set(range(1, 9))
    assert all(len(value) == 64 for value in PINNED_GAMBLER_CLASSIC_SPRITE_SHA256.values())


def test_r263_preserves_no_inference_and_canonical_boundaries() -> None:
    production = Path("bridge_vision/bridgit_primary_production.py").read_text(
        encoding="utf-8"
    )
    primary = Path("bridge_vision/bridgit_primary_video.py").read_text(
        encoding="utf-8"
    )
    assert '"VISUAL_ONLY; NO_DECK_COMPLEMENT"' in production
    assert '"canonical_promotion_allowed": False' in production
    assert "derive_fourth_hand=False" in primary
    assert 'result["hidden_hand_reconstruction_performed"] = False' in Path(
        "bridge_vision/bridgit_gambler_rank_layout.py"
    ).read_text(encoding="utf-8")
