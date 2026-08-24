from bridge_school_api.ai_worlds import WorldGenerationError, generate_worlds, parse_hand_pbn


HAND = "AKQJ.T98.765.432"


def _cards(hand):
    suits = "SHDC"
    return {suit + rank for suit, ranks in zip(suits, hand.split(".")) for rank in ranks}


def test_world_generation_is_reproducible_and_complete():
    kwargs = dict(known_seat="N", known_hand_pbn=HAND, constraints=None, count=4, seed=17)
    first = generate_worlds(**kwargs)
    second = generate_worlds(**kwargs)
    assert first == second
    assert first["complete"] is True
    assert first["constraint_class"] == "KNOWN_HAND_ONLY"
    assert len({world["fingerprint"] for world in first["worlds"]}) == 4
    for world in first["worlds"]:
        all_cards = set()
        for hand in world["hands"].values():
            cards = _cards(hand)
            assert len(cards) == 13
            assert not (all_cards & cards)
            all_cards |= cards
        assert len(all_cards) == 52
        assert world["hands"]["N"] == HAND
        assert world["pbn"].startswith("N:")
    assert len(first["constraints_sha256"]) == 64
    assert first["engine"] == "WORLD_GENERATOR"
    assert first["fallback_used"] is False


def test_world_generation_enforces_explicit_hcp_and_shape_constraints():
    result = generate_worlds(
        known_seat="N",
        known_hand_pbn=HAND,
        constraints={"seats": {"E": {"hcp": [10, 12], "suits": {"S": [4, 6]}}}},
        count=3,
        seed=91,
        max_attempts=5000,
    )
    assert result["complete"] is True
    assert result["constraint_class"] == "EXPLICIT_HARD_CONSTRAINTS"
    for world in result["worlds"]:
        east = _cards(world["hands"]["E"])
        hcp = sum({"A": 4, "K": 3, "Q": 2, "J": 1}.get(card[1], 0) for card in east)
        assert 10 <= hcp <= 12
        assert 4 <= sum(card[0] == "S" for card in east) <= 6


def test_impossible_constraints_fail_closed_without_fabricating_worlds():
    result = generate_worlds(
        known_seat="N",
        known_hand_pbn=HAND,
        constraints={"seats": {"E": {"suits": {"S": [13, 13]}}}},
        count=2,
        seed=1,
        max_attempts=20,
    )
    assert result["complete"] is False
    assert result["accepted"] == 0


def test_invalid_known_hand_and_unknown_constraint_are_rejected():
    for hand in ("AKQJ.T98.765", "AKQJ.T98.765.43"):
        try:
            parse_hand_pbn(hand)
        except WorldGenerationError:
            pass
        else:
            raise AssertionError("invalid known hand was accepted")
    try:
        generate_worlds(
            known_seat="N", known_hand_pbn=HAND,
            constraints={"seats": {"E": {"meaning": "opening"}}},
            count=1, seed=1,
        )
    except WorldGenerationError:
        pass
    else:
        raise AssertionError("unsupported inferred meaning was accepted")
