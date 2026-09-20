import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bridge_vision.bridgit_primary_video_r264 as primary_video
import bridge_vision.bridgit_rank_layout_r264 as rank_layout
from bridge_contracts.video_deal_r264 import canonicalize_video_deal


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
    import hashlib

    expected = {
        "run_drive_3_1_free_generic.py": "c105673f51596db3a7c77fbd0e6e6cc7ab595ffa8b468bfa05a33c7895ce74cf",
        "bridge_runtime_hardening_r26.py": "954a0cae740658dbbbe3bf489977ca505978e3af1eec8d6b91168f3e4cc229f4",
        "bridge_vision/bridgit_rank_layout.py": "509463ebe40f93d3a62b884b7624eab1e0ad1257ebeb525971aeea5e5882793c",
        "bridge_vision/bridgit_gambler_rank_layout.py": "6a9c1f9b442e0604d6e499bc97781db96a6ec61aabed2c9e4a3b5a7b6cde37b0",
        "bridge_vision/bridgit_primary_video.py": "a7283453f995c24979e7380512ba80c36e7b7a71b115432088bf728201d7d83d",
        "bridge_vision/bridgit_primary_production.py": "8a7bfc0e619e47b2f9b3a01d8d73361f22e77808873f581f72cb9a6994379c74",
    }
    for filename, digest in expected.items():
        assert hashlib.sha256(Path(filename).read_bytes()).hexdigest() == digest
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
    assert "install_primary_recognizer(base, token_func)" in source


def test_version_identity_changes_only_when_both_r264_gates_are_enabled(monkeypatch):
    assert primary_video._runtime_version() == primary_video.PRIMARY_VIDEO_VERSION
    monkeypatch.setattr(primary_video, "ALLOW_FOURTH_HAND_DERIVATION", True)
    assert primary_video._runtime_version() == primary_video.PRIMARY_VIDEO_VERSION
    monkeypatch.setattr(rank_layout, "TEMPORAL_CARD_UNION_ENABLED", True)
    assert (
        primary_video._runtime_version()
        == primary_video.PRIMARY_VIDEO_TEMPORAL_COMPLEMENT_VERSION
    )


def test_manual_workflow_routes_r264_without_changing_default_entrypoint():
    workflow = Path(".github/workflows/bridge-video-3.1-free.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("python run_drive_3_1_free_oidc.py") == 1
    assert workflow.count("python run_drive_3_1_free_oidc_r264.py") == 1
    assert "from run_drive_3_1_free_r264 import main as generic_main" in Path(
        "run_drive_3_1_free_oidc_r264.py"
    ).read_text(encoding="utf-8")


def test_r264_derives_only_one_wholly_absent_hand():
    ranks = "AKQJT98765432"
    deal = canonicalize_video_deal(
        {
            "hands": {
                "N": [f"{rank}S" for rank in ranks],
                "E": [f"{rank}H" for rank in ranks],
                "S": [f"{rank}D" for rank in ranks],
            }
        },
        derive_fourth_hand=True,
    ).to_dict()
    assert deal["hands"]["W"]["unknown_count"] == 0
    assert deal["card_provenance"]["W"]["observed_cards"] == []
    assert len(deal["card_provenance"]["W"]["derived_cards"]) == 13
    assert deal["derivations"][0]["provenance"] == "INFERRED_DECK_COMPLEMENT"


def test_r264_temporal_union_accepts_one_card_supporting_frame(monkeypatch):
    monkeypatch.setattr(rank_layout, "TEMPORAL_CARD_UNION_ENABLED", True)
    assert rank_layout._temporal_support_gates(
        observed_frames=2,
        required_frames=2,
        minimum_ink_support=1,
        distinct_timestamps=2,
    ) == (True, True)


def test_r264_weak_frame_miss_is_not_a_conflict(monkeypatch):
    monkeypatch.setattr(rank_layout, "TEMPORAL_CARD_UNION_ENABLED", True)
    agreed = {suit: tuple("N" * 13) for suit in rank_layout.SUITS}
    changed = dict(agreed)
    changed[rank_layout.SUITS[0]] = tuple("E" + "N" * 12)
    profile = SimpleNamespace(
        min_template_score=0.4,
        min_assignment_margin=0.2,
        min_rank_ink_fraction=0.2,
    )
    assert rank_layout._frame_assignment_issues(
        ["a" * 64, "b" * 64],
        agreed,
        [agreed, changed],
        [0.8, 0.39],
        [0.5, 0.11],
        [0.3, 0.19],
        profile,
    ) == []
    assert rank_layout._frame_assignment_issues(
        ["a" * 64, "b" * 64],
        agreed,
        [agreed, changed],
        [0.8, 0.8],
        [0.5, 0.5],
        [0.3, 0.3],
        profile,
    )[0]["reasons"] == ["deal_assignment_disagrees"]


def test_r264_virtual_slots_exist_only_for_wholly_absent_hand():
    lengths = {
        "N": {"H": 4, "C": 3, "D": 3, "S": 3},
        "E": {"H": 3, "C": 4, "D": 3, "S": 3},
        "S": {"H": 3, "C": 3, "D": 4, "S": 3},
        "W": {"H": 0, "C": 0, "D": 0, "S": 0},
    }
    visible_slots = [
        (seat, (index, 10))
        for seat, count in (("N", 4), ("E", 3), ("S", 3))
        for index in range(count)
    ]
    assignment_lengths, slots, matrix = rank_layout._assignment_inputs(
        lengths,
        "H",
        visible_slots,
        [[0.9] * 13 for _ in visible_slots],
    )
    assert assignment_lengths == {"N": 4, "E": 3, "S": 3, "W": 3}
    assert [xy for seat, xy in slots if seat == "W"] == [None, None, None]
    assert matrix[-1] == (0.0,) * 13


def test_r264_primary_accepts_strict_three_hand_candidate():
    result = {
        "status": "SHADOW_THREE_HAND_LAYOUT_CANDIDATE",
        "hidden_hand_reconstruction_performed": True,
        "integrity": {
            "cards": 39,
            "unique": 39,
            "seat_counts": {"N": 13, "E": 13, "S": 13, "W": 0},
        },
        "hands": {
            "N": {"S": list("AKQJT98765432")},
            "E": {"H": list("AKQJT98765432")},
            "S": {"D": list("AKQJT98765432")},
            "W": {},
        },
        "uncertainties": [],
        "frame_assignment_issues": [],
        "evidence": {"per_frame_deal_agreement": True},
    }
    assert primary_video._accepted_primary_result(result) is True
    result["hidden_hand_reconstruction_performed"] = False
    assert primary_video._accepted_primary_result(result) is False


def test_r264_partial_fourth_hand_fails_closed():
    ranks = "AKQJT98765432"
    with pytest.raises(ValueError, match="exactly three complete hands"):
        canonicalize_video_deal(
            {
                "hands": {
                    "N": [f"{rank}S" for rank in ranks],
                    "E": [f"{rank}H" for rank in ranks],
                    "S": [f"{rank}D" for rank in ranks],
                    "W": ["AC"],
                }
            },
            derive_fourth_hand=True,
        )
