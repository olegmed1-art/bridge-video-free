"""Synthetic statements and deck oracle; not an ASR/video accuracy benchmark."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bridge_contracts.video_deal_r264 import canonicalize_video_deal
from bridge_vision import bridgit_primary_production_r265 as adapter
from bridge_vision.teacher_card_reconstruction_r265 import (
    extract_teacher_card_declarations, reconstruct_deal,
)


def teacher(text, **overrides):
    return dict(text=text, start=130.0, end=132.0, speaker="SYNTHETIC_TEACHER",
                speaker_role_candidate="teacher", speaker_role_confidence=.95,
                unreliable=False) | overrides


@pytest.mark.parametrize("text", [
    "У севера нет туза пик.",
    "North does not have the ace of spades.",
    "If North has the ace of spades, the contract makes.",
    "Does North have the ace of spades?",
    "Does North have the ace of spades",  # ASR can omit question marks.
    "У севера туз пик?", "У севера туз пик？", "Разве у севера туз пик",
    "У севера, возможно, туз пик.", "У севера был туз пик.",
    "У севера туз пик, если предположение верно.",
    "North has no ace of spades.", "North has probably the ace of spades.",
    "North has the ace of spades, or maybe not.",
    "North has the ace of spades. The king of hearts.",
    "North has the ace of spades not the king of hearts.",
])
def test_nonassertions_never_enter_card_evidence(text):
    extraction = extract_teacher_card_declarations([teacher(text)])
    assert extraction["declarations"] == []
    assert extraction["rejected"][0]["reason"] == "NON_ASSERTIVE_CARD_STATEMENT"
    result = reconstruct_deal({}, extraction["declarations"], deal_timestamp_seconds=131)
    assert result["unique_cards"] == 0
    assert result["canonical_promotion_allowed"] is False


@pytest.mark.parametrize("text,seat,cards", [
    ("У севера туз пик.", "N", {"AS"}),
    ("У запада есть король треф и двойка червей.", "W", {"KC", "2H"}),
    ("North has the ace of spades and the king of hearts.", "N", {"AS", "KH"}),
    ("East holds an ace of diamonds", "E", {"AD"}),
    ("South has the ace of spades, the king of clubs.", "S", {"AS", "KC"}),
])
def test_direct_assertions_keep_speech_provenance(text, seat, cards):
    extraction = extract_teacher_card_declarations([teacher(text)])
    assert {(d["seat"], d["card"]) for d in extraction["declarations"]} == {(seat, card) for card in cards}
    result = reconstruct_deal({}, extraction["declarations"], deal_timestamp_seconds=131)
    assert set(result["deal"]["card_provenance"][seat]["TEACHER_SPEECH"]) == cards
    assert result["deal"]["card_provenance"][seat]["VISUAL"] == []


@pytest.mark.parametrize("confidence,accepted", [(.899999, False), (.9, True), (1, True), (True, False)])
def test_teacher_confidence_boundary(confidence, accepted):
    result = extract_teacher_card_declarations([teacher("North has the ace of spades.", speaker_role_confidence=confidence)])
    assert bool(result["declarations"]) is accepted


@pytest.mark.parametrize("timestamp,accepted", [(9.999, False), (10, True), (252, True), (252.001, False)])
def test_speech_time_window_boundary(timestamp, accepted):
    declarations = extract_teacher_card_declarations([teacher("North has the ace of spades.")])["declarations"]
    result = reconstruct_deal({}, declarations, deal_timestamp_seconds=timestamp)
    assert (result["unique_cards"] == 1) is accepted


@pytest.mark.parametrize("timestamp", [None, True, -1, float("nan"), float("inf"), "invalid"])
def test_invalid_deal_time_never_admits_speech(timestamp):
    declarations = extract_teacher_card_declarations([teacher("North has the ace of spades.")])["declarations"]
    result = reconstruct_deal({}, declarations, deal_timestamp_seconds=timestamp)
    assert result["unique_cards"] == 0


@pytest.mark.parametrize("start,end", [(True, 2), (1, False), (-1, 1), (2, 2), (3, 2), (float("nan"), 2)])
def test_invalid_asr_interval_is_rejected(start, end):
    result = extract_teacher_card_declarations([teacher("North has the ace of spades.", start=start, end=end)])
    assert result["declarations"] == []
    assert result["rejected"][0]["reason"] == "INVALID_TIMELINE"


def installed_base(monkeypatch, deal, declarations=()):
    base = SimpleNamespace(PROMPT="", obtain_transcript=lambda *a: ([], {}, []),
        derive_deals_decisions=lambda *a: ([deal], []), master_analysis_payload=lambda *a, **k: {})
    monkeypatch.setattr(adapter, "_INSTALLED_BASE_IDS", set())
    monkeypatch.setattr(adapter, "_STATE", {"extraction": {"declarations": list(declarations)}})
    monkeypatch.setattr(adapter.previous, "_STATE", {"shots": [{"evidence_id": "frame", "time": 131}]})
    adapter.install(base, lambda: "unused")
    return base


@pytest.mark.parametrize("missing", list("NESW"))
@pytest.mark.parametrize("visible_count", [0, 1, 12, 13])
def test_inferred_fourth_hand_never_becomes_visual_on_repeated_derive(monkeypatch, missing, visible_count):
    deck = [rank + suit for suit in "SHDC" for rank in "AKQJT98765432"]
    expected = {seat: deck[i::4] for i, seat in enumerate("NESW")}
    visual = {seat: cards[:visible_count] if seat == missing else cards for seat, cards in expected.items()}
    deal = {"hands": expected, "visual_hands": visual, "hidden_hand_reconstruction_performed": visible_count < 13,
            "inferred_seats": [missing] if visible_count < 13 else [],
            "recognizer": {"version": "r264"}, "evidence": ["frame"]}
    if visible_count == 0:
        deal["canonical_deal"] = canonicalize_video_deal({"hands": visual}, derive_fourth_hand=True).to_dict()
    base = installed_base(monkeypatch, deal)
    for _ in range(2):
        result = base.derive_deals_decisions([], "synthetic")[0][0]
        provenance = result["canonical_deal"]["card_provenance"][missing]
        assert set(provenance["VISUAL"]) == set(visual[missing])
        assert set(provenance["INFERRED_DECK_COMPLEMENT"]) == set(expected[missing]) - set(visual[missing])
        assert result["visual_hands"] == visual
        assert {s: set(result["canonical_deal"]["hands"][s]["cards"]) for s in "NESW"} == {s: set(c) for s, c in expected.items()}
        assert result["reconstruction"]["canonical_promotion_allowed"] is False


@pytest.mark.parametrize("conflict", [False, True])
def test_partial_and_conflict_reconstruction_are_repeatable(monkeypatch, conflict):
    declarations = []
    if conflict:
        declarations = extract_teacher_card_declarations([
            teacher("East has the ace of hearts."), teacher("West has the ace of hearts.")
        ])["declarations"]
    deal = {"hands": {"N": ["AS"]}, "recognizer": {"version": "r264"}, "evidence": ["frame"]}
    base = installed_base(monkeypatch, deal, declarations)
    first = deepcopy(base.derive_deals_decisions([], "synthetic")[0][0])
    second = base.derive_deals_decisions([], "synthetic")[0][0]
    assert first == second
    assert second["reconstruction"]["status"] == ("UNRESOLVED_CONFLICT" if conflict else "PARTIAL_WITH_EVIDENCE")
    assert second["visual_hands"] == {"N": ["AS"], "E": [], "S": [], "W": []}


@pytest.mark.parametrize("marker", [
    {"hidden_hand_reconstruction_performed": True}, {"inferred_seats": ["W"]},
    {"canonical_deal": {"derivations": [{"seat": "W"}]}},
    {"canonical_deal": {"card_provenance": {"W": {"derived_cards": ["AC"]}}}},
    {"canonical_deal": {"card_provenance": {"W": {"TEACHER_SPEECH": ["AC"]}}}},
    {"reconstruction": {"status": "PARTIAL_WITH_EVIDENCE"}},
])
def test_missing_observation_boundary_cannot_relabel_nonvisual_cards(marker):
    with pytest.raises(ValueError, match="explicit visual_hands"):
        adapter._visual_hands({"hands": {"W": ["AC"]}, **marker})
