import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_analyzer_v3 import Observability
from bridge_school_api.tournament_bridge_co_il_v3 import normalize_bridge_co_il_facts
from bridge_school_api.tournament_adapters_v3 import TournamentAdapterError


DATA = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def test_30041_real_facts_normalize_fail_closed():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    batch, meta = normalize_bridge_co_il_facts(source)

    assert batch.event_id == "30041"
    assert batch.session_id == "round:2"
    assert batch.scoring == "MP"
    assert len(batch.deals) == 24
    assert meta.target_student_name == "Диана Векслер"
    assert meta.final_percentage == 54.57
    assert meta.rank == 8
    assert meta.field_size == 23

    assert len({deal.deal_id for deal in batch.deals}) == 24
    assert all(sum(len(cards) for cards in deal.hands.values()) == 52 for deal in batch.deals)
    assert all(deal.play_record is None for deal in batch.deals)
    assert all(deal.auction is None for deal in batch.deals)

    played = [deal for deal in batch.deals if deal.contract]
    assert len(played) == 21
    assert all(deal.deal_id.startswith("30041:round:2:") for deal in batch.deals)

    # No card-by-card source means play attribution must remain unavailable upstream.
    from bridge_school_api.tournament_analyzer_v3 import analysis_observability
    assert all(analysis_observability(deal) is Observability.NOT_OBSERVABLE for deal in batch.deals)


def test_adapter_rejects_wrong_provider_identity():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    source["tournament"]["provider_native_key"] = "30041"
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)


def test_adapter_rejects_corrupt_hand():
    source = json.loads(DATA.read_text(encoding="utf-8"))
    row = source["rows"][0].split("|")
    n_index = source["columns"].index("N")
    row[n_index] = "AKQJ.T98.765.32"  # 12 cards
    source["rows"][0] = "|".join(row)
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)
