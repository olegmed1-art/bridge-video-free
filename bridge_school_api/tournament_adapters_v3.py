from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .tournament_analyzer_v3 import TournamentDeal, validate_deal_integrity


class TournamentAdapterError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedTournamentBatch:
    event_id: str
    session_id: str
    deals: tuple[TournamentDeal, ...]
    scoring: str | None
    source: Mapping[str, Any]


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if value is None or not str(value).strip():
        raise TournamentAdapterError(f"missing required field: {key}")
    return str(value).strip()


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise TournamentAdapterError("boolean is not a valid integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TournamentAdapterError(f"invalid integer: {value!r}") from exc


def normalize_structured_deal(
    row: Mapping[str, Any],
    *,
    event_id: str,
    session_id: str,
    source_provenance: Mapping[str, Any] | None = None,
) -> TournamentDeal:
    """Normalize already-extracted tournament facts into the v3 core.

    This adapter intentionally does not scrape, infer hidden cards, invent an auction,
    or derive a play record. Upstream source adapters remain responsible for extraction.
    """
    board_number = _optional_int(row.get("board_number"))
    if board_number is None or board_number <= 0:
        raise TournamentAdapterError("board_number must be a positive integer")

    hands = row.get("hands")
    if not isinstance(hands, Mapping):
        raise TournamentAdapterError("hands must be a mapping of N/E/S/W to card sequences")

    auction = row.get("auction")
    if auction is not None and (isinstance(auction, (str, bytes)) or not isinstance(auction, Sequence)):
        raise TournamentAdapterError("auction must be a sequence or null")

    play_record = row.get("play_record")
    if play_record is not None and (
        isinstance(play_record, (str, bytes)) or not isinstance(play_record, Sequence)
    ):
        raise TournamentAdapterError("play_record must be a sequence or null")

    deal = TournamentDeal(
        event_id=_required_text({"event_id": event_id}, "event_id"),
        session_id=_required_text({"session_id": session_id}, "session_id"),
        board_number=board_number,
        hands=hands,
        dealer=str(row["dealer"]).strip().upper() if row.get("dealer") not in (None, "") else None,
        vulnerability=str(row["vulnerability"]).strip() if row.get("vulnerability") not in (None, "") else None,
        auction=tuple(str(x) for x in auction) if auction is not None else None,
        contract=str(row["contract"]).strip() if row.get("contract") not in (None, "") else None,
        declarer=str(row["declarer"]).strip().upper() if row.get("declarer") not in (None, "") else None,
        opening_lead=str(row["opening_lead"]).strip().upper() if row.get("opening_lead") not in (None, "") else None,
        score=_optional_int(row.get("score")),
        datum=float(row["datum"]) if row.get("datum") not in (None, "") else None,
        play_record=tuple(str(x) for x in play_record) if play_record is not None else None,
        source_provenance=dict(source_provenance or {}),
    )
    validate_deal_integrity(deal)
    return deal


def normalize_structured_batch(
    rows: Sequence[Mapping[str, Any]],
    *,
    event_id: str,
    session_id: str,
    scoring: str | None = None,
    source: Mapping[str, Any] | None = None,
) -> NormalizedTournamentBatch:
    if not rows:
        raise TournamentAdapterError("at least one source row is required")

    deals: list[TournamentDeal] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        provenance = {**dict(source or {}), "row_index": index}
        deal = normalize_structured_deal(
            row,
            event_id=event_id,
            session_id=session_id,
            source_provenance=provenance,
        )
        if deal.deal_id in seen:
            raise TournamentAdapterError(f"duplicate scoped deal identity: {deal.deal_id}")
        seen.add(deal.deal_id)
        deals.append(deal)

    return NormalizedTournamentBatch(
        event_id=event_id,
        session_id=session_id,
        deals=tuple(deals),
        scoring=scoring,
        source=dict(source or {}),
    )
