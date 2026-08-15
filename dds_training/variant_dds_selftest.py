from __future__ import annotations

import json

from dds_engine import contract_tricks_batch, opening_lead_scores
from variants import _parse_deal, permute_suits_task, perturb_task, rotate_task


def _validate_complete_deal(pbn: str) -> None:
    hands = _parse_deal(pbn)
    seen: set[tuple[int, str]] = set()
    for seat in range(4):
        assert sum(len(s) for s in hands[seat]) == 13
        for suit, cards in enumerate(hands[seat]):
            for rank in cards:
                key = (suit, rank)
                assert key not in seen, key
                seen.add(key)
    assert len(seen) == 52


def main() -> None:
    task = {
        "task_id": "DDS-SYM",
        "deal_id": "DDS-SYM-DEAL",
        "task_type": "opening_lead",
        "split": "train",
        "deal": "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3",
        "declarer": 2,
        "leader": 3,
        "strain": 0,
        "strain_name": "S",
    }
    _validate_complete_deal(task["deal"])

    rot = rotate_task(task, 1)
    suit_map = (1, 0, 3, 2)
    perm = permute_suits_task(task, suit_map)
    pert1 = perturb_task(task, "dds-p1")
    pert2 = perturb_task(task, "dds-p2")
    for v in (rot, perm, pert1, pert2):
        _validate_complete_deal(v["deal"])

    original_table, rotated_table, permuted_table = contract_tricks_batch(
        [task["deal"], rot["deal"], perm["deal"]]
    )

    # Seat rotation must rotate the table columns and preserve every strain value.
    for strain in range(5):
        for seat in range(4):
            assert int(original_table[strain][seat]) == int(rotated_table[strain][(seat + 1) % 4]), (
                strain, seat, original_table, rotated_table
            )

    # Suit renaming must move the strain row by the same permutation. NT is fixed.
    for old_strain in range(4):
        new_strain = suit_map[old_strain]
        for seat in range(4):
            assert int(original_table[old_strain][seat]) == int(permuted_table[new_strain][seat]), (
                old_strain, new_strain, seat
            )
    for seat in range(4):
        assert int(original_table[4][seat]) == int(permuted_table[4][seat])

    # Opening-lead optimum is invariant under pure seat rotation.
    original_leads = opening_lead_scores(task["deal"], task["strain"], task["declarer"])
    rotated_leads = opening_lead_scores(rot["deal"], rot["strain"], rot["declarer"])
    assert max(original_leads.values()) == max(rotated_leads.values())

    print(json.dumps({
        "ok": True,
        "dds_rotation_table_invariant": True,
        "dds_suit_permutation_invariant": True,
        "opening_lead_value_rotation_invariant": True,
        "perturbations_are_complete_52_card_deals": True,
        "derived_root_split": rot["source_root_split"],
    }, indent=2))


if __name__ == "__main__":
    main()
