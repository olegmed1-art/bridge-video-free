from types import SimpleNamespace

import numpy as np

import bridge_vision.bridgit_primary_video_r264 as primary
from bridge_vision import bridgit_rank_layout_r264 as rank_layout
from bridge_vision.deal_review_pdf import build_deal_review_views


def _matrix_for(rank_index: int):
    values = [0.05] * len(rank_layout.RANKS)
    values[rank_index] = 0.95
    return values


def _component(rank_index: int):
    assignment = _matrix_for(rank_index)
    raw = np.asarray([_matrix_for(rank_index), _matrix_for(rank_index)])
    ink = np.full((2, len(rank_layout.RANKS)), 0.5)
    return {
        "assignment": assignment,
        "per_frame_raw": raw,
        "per_frame_ink": ink,
    }


def _geometry(*, south=0, north=0):
    lengths = {
        seat: {suit: 0 for suit in rank_layout.SUITS}
        for seat in rank_layout.SEATS
    }
    lengths["S"]["S"] = south
    lengths["N"]["S"] = north
    anchors = {
        seat: {suit: (0, 0) for suit in rank_layout.SUITS}
        for seat in rank_layout.SEATS
    }
    return {
        "lengths": lengths,
        "anchors": anchors,
        "horizontal_steps": {"N": 1.0, "S": 1.0},
        "side_chains": {
            seat: {suit: () for suit in rank_layout.SUITS}
            for seat in ("W", "E")
        },
    }


def _independent_frames():
    return np.asarray([0], dtype="uint8"), np.asarray([1], dtype="uint8")


def test_ordered_subset_does_not_invent_unseen_ranks():
    alternatives = primary._ordered_subset_assignments(
        [_matrix_for(0), _matrix_for(2)]
    )
    assert alternatives[0][1] == (0, 2)
    assert len(alternatives[0][1]) == 2


def test_partial_pair_retains_proven_cards_and_unknown_elsewhere(monkeypatch):
    monkeypatch.setattr(primary, "_registered_candidate", lambda image, profile: image)
    components = iter([_component(0), _component(2)])
    monkeypatch.setattr(
        rank_layout,
        "_slot_score_components",
        lambda frames, bank, xy, profile: next(components),
    )
    profile = SimpleNamespace(
        min_assignment_margin=0.2,
        min_template_score=0.4,
        min_rank_ink_fraction=0.2,
    )
    first, second = _independent_frames()
    result = primary._recognize_partial_pair(
        first,
        second,
        bank={},
        profile=profile,
        geometry=_geometry(south=2),
        frame_sha256s=["a" * 64, "b" * 64],
        timestamps_ms=[1000, 1600],
    )
    assert result["status"] == "PARTIAL_VISUAL_OBSERVATION"
    assert result["hands"] == {"N": [], "E": [], "S": ["AS", "QS"], "W": []}
    assert result["integrity"] == {
        "cards": 2,
        "unique": 2,
        "seat_counts": {"N": 0, "E": 0, "S": 2, "W": 0},
    }
    assert {item["source"] for item in result["observations"]} == {"VISUAL"}


def test_partial_pair_fails_closed_when_one_card_has_two_owners(monkeypatch):
    monkeypatch.setattr(primary, "_registered_candidate", lambda image, profile: image)
    components = iter([_component(0), _component(0)])
    monkeypatch.setattr(
        rank_layout,
        "_slot_score_components",
        lambda frames, bank, xy, profile: next(components),
    )
    profile = SimpleNamespace(
        min_assignment_margin=0.2,
        min_template_score=0.4,
        min_rank_ink_fraction=0.2,
    )
    first, second = _independent_frames()
    result = primary._recognize_partial_pair(
        first,
        second,
        bank={},
        profile=profile,
        geometry=_geometry(south=1, north=1),
        frame_sha256s=["a" * 64, "b" * 64],
        timestamps_ms=[1000, 1600],
    )
    assert result["status"] == "PARTIAL_VISUAL_CONFLICT"
    assert result["conflicts"] == [{"card": "AS", "seats": ["N", "S"]}]


def test_r266_flag_does_not_change_earlier_runtime_identity(monkeypatch):
    monkeypatch.setattr(primary, "ALLOW_PARTIAL_OBSERVATIONS", False)
    assert primary._runtime_version() != primary.PRIMARY_VIDEO_PARTIAL_OBSERVATION_VERSION
    monkeypatch.setattr(primary, "ALLOW_PARTIAL_OBSERVATIONS", True)
    assert primary._runtime_version() == primary.PRIMARY_VIDEO_PARTIAL_OBSERVATION_VERSION


def test_r266_pdf_omits_zero_card_semantic_candidates():
    master = {
        "principles": {"deal_review_requires_card_evidence": True},
        "deals": [
            {
                "deal_id": "semantic-only",
                "status": "candidate",
                "hands": {seat: None for seat in rank_layout.SEATS},
                "evidence": [],
            },
            {
                "deal_id": "visual-partial",
                "status": "VISUAL_PRIMARY_PARTIAL_OBSERVATION",
                "hands": {"N": [], "E": [], "S": ["AS"], "W": []},
                "evidence": [],
            },
        ],
    }
    views = build_deal_review_views(master, [])
    assert [view["deal_id"] for view in views] == ["visual-partial"]
    assert views[0]["observed_count"] == 1


def test_r266_workflow_is_bounded_and_not_the_default():
    workflow = open(".github/workflows/bridge-video-3.1-free.yml", encoding="utf-8").read()
    assert 'default: "3.1-free-r26.3"' in workflow
    assert 'inputs.algorithm_revision == \'3.1-free-r26.6\'' in workflow
    assert "python run_drive_3_1_free_oidc_r266.py" in workflow


def test_manual_non_persistent_run_disables_all_neon_checkpoint_writes():
    workflow = open(".github/workflows/bridge-video-3.1-free.yml", encoding="utf-8").read()
    assert (
        "if: steps.terminal.outputs.already_completed != 'true' && "
        "env.BRIDGE_PERSIST_DATABASE == 'true'"
    ) in workflow
    assert workflow.count('if [ "$BRIDGE_PERSIST_DATABASE" = "true" ]; then') >= 2
    assert (
        "- name: Record durable workflow final checkpoint\n"
        "        if: always() && env.BRIDGE_PERSIST_DATABASE == 'true'"
    ) in workflow
