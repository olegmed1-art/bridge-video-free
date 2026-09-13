from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .tournament_adapters_v3 import NormalizedTournamentBatch, TournamentAdapterError
from .tournament_real_sources_v3 import normalize_30041_facts


@dataclass(frozen=True)
class BridgeCoIlFactsMeta:
    event_id: str
    session_id: str
    scoring: str | None
    pair_key: str | None
    target_student_name: str | None
    final_percentage: float | None
    rank: int | None
    field_size: int | None


def _optional_text(mapping: Mapping[str, Any], key: str) -> str | None:
    value = mapping.get(key)
    if value in (None, ""):
        return None
    return str(value)


def _optional_float(mapping: Mapping[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TournamentAdapterError(f"boolean is not a valid {key}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise TournamentAdapterError(f"invalid {key}: {value!r}") from exc


def _optional_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TournamentAdapterError(f"boolean is not a valid {key}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentAdapterError(f"invalid {key}: {value!r}") from exc


def normalize_bridge_co_il_facts(
    source: Mapping[str, Any],
) -> tuple[NormalizedTournamentBatch, BridgeCoIlFactsMeta]:
    """Compatibility entrypoint for the currently audited bridge.co.il facts source.

    The real-evidence layer is the single authority for source identity and parsing.
    This wrapper deliberately delegates to ``normalize_30041_facts`` so the same
    exact origin SHA, provider key, FACTS_ONLY policy, 24-board count, PBN parsing,
    provenance and canonical ``round-2`` deal identity are enforced everywhere.

    A future bridge.co.il event must receive its own source-specific contract before
    this entrypoint can accept it. It must not silently broaden to unpinned data.
    """
    batch = normalize_30041_facts(source)

    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        # normalize_30041_facts already checks this, but keep the metadata boundary
        # independently fail-closed for callers of this compatibility entrypoint.
        raise TournamentAdapterError("missing tournament metadata")

    meta = BridgeCoIlFactsMeta(
        event_id=batch.event_id,
        session_id=batch.session_id,
        scoring=batch.scoring,
        pair_key=_optional_text(tournament, "pair_key"),
        target_student_name=_optional_text(tournament, "target_student_name"),
        final_percentage=_optional_float(tournament, "final_percentage"),
        rank=_optional_int(tournament, "rank"),
        field_size=_optional_int(tournament, "field_size"),
    )
    return batch, meta
