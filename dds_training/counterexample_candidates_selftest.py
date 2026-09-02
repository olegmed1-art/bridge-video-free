from __future__ import annotations

import json

from counterexample_candidates import candidate_from_pair


def main() -> None:
    source_ct = {"task_id": "A", "deal_id": "D", "task_type": "contract_tricks"}
    variant_ct = {
        "task_id": "A-P", "deal_id": "D-P", "task_type": "contract_tricks",
        "evidence_type": "perturbation", "variant_kind": "same_suit_rank_swap",
    }
    ct = candidate_from_pair(source_ct, variant_ct, {"dds_tricks": 9}, {"dds_tricks": 10})
    assert ct and ct["magnitude"] == 1
    assert ct["verified"] is False

    source_ol = {"task_id": "B", "deal_id": "E", "task_type": "opening_lead"}
    variant_ol = {
        "task_id": "B-P", "deal_id": "E-P", "task_type": "opening_lead",
        "evidence_type": "perturbation", "variant_kind": "same_suit_rank_swap",
    }
    ol = candidate_from_pair(
        source_ol,
        variant_ol,
        {"optimal_cards": ["SA", "SK"]},
        {"optimal_cards": ["H2"]},
        {"card": "SA"},
    )
    assert ol and ol["chosen_card_flipped_from_optimal"] is True
    assert ol["optimal_set_jaccard"] == 0.0
    assert ol["requires_blind_discrimination"] is True

    no_change = candidate_from_pair(
        source_ct, variant_ct, {"dds_tricks": 9}, {"dds_tricks": 9}
    )
    assert no_change is None

    print(json.dumps({
        "ok": True,
        "contract_value_flip": True,
        "opening_lead_flip": True,
        "blind_verification_required": True,
    }, indent=2))


if __name__ == "__main__":
    main()
