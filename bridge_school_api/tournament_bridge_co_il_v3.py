from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .tournament_adapters_v3 import NormalizedTournamentBatch, TournamentAdapterError, normalize_structured_batch


SEATS = ("N", "E", "S", "W")
SUITS = ("S", "H", "D", "C")


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


def _hand_to_cards(pbn_hand: str) -> tuple[str, ...]:
    parts = pbn_hand.strip().upper().split(".")
    if len(parts) != 4:
        raise TournamentAdapterError(f"invalid PBN hand: {pbn_hand!r}")
    cards: list[str] = []
    for suit, ranks in zip(SUITS, parts, strict=True):
        if ranks in ("", "-"):
            continue
        for rank in ranks:
            if rank not in "23456789TJQKA":
                raise TournamentAdapterError(f"invalid rank {rank!r} in hand {pbn_hand!r}")
            cards.append(rank + suit)
    if len(cards) != 13:
        raise TournamentAdapterError(f"hand must contain 13 cards, got {len(cards)}: {pbn_hand!r}")
    return tuple(cards)


def _row_dict(columns: Sequence[str], raw_row: str) -> dict[str, str]:
    values = raw_row.split("|")
    if len(values) != len(columns):
        raise TournamentAdapterError(
            f"row has {len(values)} fields, expected {len(columns)}: {raw_row!r}"
        )
    return dict(zip(columns, values, strict=True))


def _parse_provider_key(value: str) -> tuple[str, str]:
    # Expected audited key: bridge.co.il:event:30041:round:2
    parts = value.split(":")
    if len(parts) != 5 or parts[0] != "bridge.co.il" or parts[1] != "event" or parts[3] != "round":
        raise TournamentAdapterError(f"unsupported provider_native_key: {value!r}")
    return parts[2], f"round:{parts[4]}"


def normalize_bridge_co_il_facts(source: Mapping[str, Any]) -> tuple[NormalizedTournamentBatch, BridgeCoIlFactsMeta]:
    """Normalize the already-audited bridge-tournament-facts-v1 artifact.

    No scraping is performed. Recommended auctions are not treated as observed auctions,
    and absent play records remain absent.
    """
    if source.get("schema") != "bridge-tournament-facts-v1":
        raise TournamentAdapterError("unsupported bridge.co.il tournament facts schema")

    tournament = source.get("tournament")
    if not isinstance(tournament, Mapping):
        raise TournamentAdapterError("missing tournament metadata")
    provider_key = str(tournament.get("provider_native_key") or "")
    event_id, session_id = _parse_provider_key(provider_key)

    columns = source.get("columns")
    rows = source.get("rows")
    if not isinstance(columns, Sequence) or isinstance(columns, (str, bytes)):
        raise TournamentAdapterError("columns must be a sequence")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)) or not rows:
        raise TournamentAdapterError("rows must be a non-empty sequence")

    required = {"board", "dealer", "vulnerability", "N", "E", "S", "W", "status", "contract", "declarer", "opening_lead", "pair_score", "pair_percentage"}
    if not required.issubset(set(str(x) for x in columns)):
        missing = sorted(required - set(str(x) for x in columns))
        raise TournamentAdapterError(f"missing required columns: {missing}")

    normalized_rows: list[dict[str, Any]] = []
    for raw in rows:
        if not isinstance(raw, str):
            raise TournamentAdapterError("facts rows must be pipe-delimited strings")
        row = _row_dict([str(x) for x in columns], raw)
        hands = {seat: _hand_to_cards(row[seat]) for seat in SEATS}
        normalized_rows.append(
            {
                "board_number": int(row["board"]),
                "hands": hands,
                "dealer": row["dealer"] or None,
                "vulnerability": row["vulnerability"] or None,
                # Historical report has no observed auction/play record.
                "auction": None,
                "contract": row["contract"] or None,
                "declarer": row["declarer"] or None,
                "opening_lead": row["opening_lead"] or None,
                "score": int(row["pair_score"]) if row["pair_score"] else None,
                "datum": float(row["pair_percentage"]) if row["pair_percentage"] else None,
                "play_record": None,
            }
        )

    source_meta = source.get("source") if isinstance(source.get("source"), Mapping) else {}
    batch = normalize_structured_batch(
        normalized_rows,
        event_id=event_id,
        session_id=session_id,
        scoring=str(tournament.get("scoring")) if tournament.get("scoring") else None,
        source={
            "adapter": "bridge.co.il:bridge-tournament-facts-v1",
            "provider_native_key": provider_key,
            "source_sha256": source_meta.get("sha256"),
            "drive_id": source_meta.get("drive_id"),
            "policy_mode": (source.get("policy") or {}).get("mode") if isinstance(source.get("policy"), Mapping) else None,
        },
    )

    meta = BridgeCoIlFactsMeta(
        event_id=event_id,
        session_id=session_id,
        scoring=batch.scoring,
        pair_key=str(tournament.get("pair_key")) if tournament.get("pair_key") else None,
        target_student_name=str(tournament.get("target_student_name")) if tournament.get("target_student_name") else None,
        final_percentage=float(tournament["final_percentage"]) if tournament.get("final_percentage") is not None else None,
        rank=int(tournament["rank"]) if tournament.get("rank") is not None else None,
        field_size=int(tournament["field_size"]) if tournament.get("field_size") is not None else None,
    )
    return batch, meta
