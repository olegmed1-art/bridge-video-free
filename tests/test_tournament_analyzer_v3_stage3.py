import copy
import json
from pathlib import Path

import pytest

from bridge_school_api.tournament_adapters_v3 import TournamentAdapterError
from bridge_school_api.tournament_analyzer_v3 import Observability, analysis_observability
from bridge_school_api.tournament_bridge_co_il_v3 import normalize_bridge_co_il_facts
from bridge_school_api.tournament_real_sources_v3 import normalize_30041_facts


DATA = Path("data/tournaments/tournament_30041_round2_diana_facts_v1.json")


def _source() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_bridge_co_il_entrypoint_is_exact_canonical_30041_adapter():
    source = _source()
    canonical = normalize_30041_facts(source)
    batch, meta = normalize_bridge_co_il_facts(source)

    # There must be only one scoped identity convention for this audited source.
    assert batch == canonical
    assert batch.event_id == "30041"
    assert batch.session_id == "round-2"
    assert batch.scoring == "MP"
    assert len(batch.deals) == 24
    assert len({deal.deal_id for deal in batch.deals}) == 24
    assert all(deal.deal_id.startswith("30041:round-2:") for deal in batch.deals)

    assert meta.event_id == batch.event_id
    assert meta.session_id == batch.session_id
    assert meta.target_student_name == "Диана Векслер"
    assert meta.final_percentage == 54.57
    assert meta.rank == 8
    assert meta.field_size == 23

    assert all(sum(len(cards) for cards in deal.hands.values()) == 52 for deal in batch.deals)
    assert all(deal.play_record is None for deal in batch.deals)
    assert all(deal.auction is None for deal in batch.deals)
    assert len([deal for deal in batch.deals if deal.contract]) == 21
    assert all(analysis_observability(deal) is Observability.NOT_OBSERVABLE for deal in batch.deals)

    # Canonical source-specific provenance must survive the compatibility wrapper.
    assert batch.source["kind"] == "audited_extract"
    assert batch.deals[0].source_provenance["row_index"] == 0
    assert batch.deals[0].source_provenance["status"] == "average"


def test_adapter_rejects_even_well_formed_but_unapproved_provider_identity():
    source = _source()
    source["tournament"]["provider_native_key"] = "bridge.co.il:event:99999:round:1"
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)


def test_adapter_rejects_wrong_origin_hash():
    source = _source()
    source["source"]["sha256"] = "0" * 64
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)


def test_adapter_rejects_weakened_facts_only_policy():
    source = _source()
    source["policy"]["student_observation_writes_allowed"] = True
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)


def test_adapter_rejects_corrupt_hand():
    source = _source()
    row = source["rows"][0].split("|")
    n_index = source["columns"].index("N")
    row[n_index] = "AKQJ.T98.765.32"  # 12 cards
    source["rows"][0] = "|".join(row)
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)


def test_metadata_numbers_fail_closed_on_boolean_values():
    source = _source()
    source["tournament"]["rank"] = True
    with pytest.raises(TournamentAdapterError):
        normalize_bridge_co_il_facts(source)
