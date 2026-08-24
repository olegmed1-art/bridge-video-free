"""Fail-closed DDS3 scoring for completed BEN auction rollouts.

BEN remains the source of auction-policy decisions. DDS3 remains the source of
double-dummy trick counts. Duplicate scoring is deterministic bridge arithmetic
over the validated contract, declarer, vulnerability, and DDS3 table value.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


SEATS = ("N", "E", "S", "W")
_CONTRACT = re.compile(r"^([1-7])([CDHSN])((?:XX|X)?)$")


class AuctionScoringError(ValueError):
    pass


def normalize_vulnerability(raw: Any) -> str:
    value = str(raw or "").strip().upper().replace("-", "").replace("_", "")
    aliases = {
        "": "NONE",
        "NONE": "NONE",
        "LOVE": "NONE",
        "NS": "NS",
        "EW": "EW",
        "BOTH": "BOTH",
        "ALL": "BOTH",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise AuctionScoringError("vulnerability is invalid") from exc


def _declarer_vulnerable(declarer: str, vulnerability: str) -> bool:
    if vulnerability == "BOTH":
        return True
    return vulnerability == ("NS" if declarer in {"N", "S"} else "EW")


def _contract_parts(contract: Any) -> tuple[int, str, str]:
    value = str(contract or "").strip().upper().replace("NT", "N")
    match = _CONTRACT.fullmatch(value)
    if not match:
        raise AuctionScoringError("contract is invalid")
    return int(match.group(1)), match.group(2), match.group(3)


def score_duplicate_contract(
    *,
    contract: Any,
    declarer: Any,
    tricks: Any,
    vulnerability: Any,
) -> dict[str, Any]:
    """Return duplicate score with positive values always representing NS."""
    level, strain, doubling = _contract_parts(contract)
    seat = str(declarer or "").strip().upper()
    if seat not in SEATS:
        raise AuctionScoringError("declarer is invalid")
    if isinstance(tricks, bool) or not isinstance(tricks, int) or not 0 <= tricks <= 13:
        raise AuctionScoringError("DDS3 trick count is invalid")
    vul = normalize_vulnerability(vulnerability)
    vulnerable = _declarer_vulnerable(seat, vul)
    required = level + 6
    delta = tricks - required
    multiplier = {"": 1, "X": 2, "XX": 4}[doubling]

    if delta >= 0:
        if strain in {"C", "D"}:
            undoubled_contract_points = level * 20
            undoubled_overtrick = 20
        elif strain in {"H", "S"}:
            undoubled_contract_points = level * 30
            undoubled_overtrick = 30
        else:
            undoubled_contract_points = 40 + (level - 1) * 30
            undoubled_overtrick = 30

        contract_points = undoubled_contract_points * multiplier
        if multiplier == 1:
            overtrick_points = delta * undoubled_overtrick
        elif multiplier == 2:
            overtrick_points = delta * (200 if vulnerable else 100)
        else:
            overtrick_points = delta * (400 if vulnerable else 200)

        game_or_partscore_bonus = (500 if vulnerable else 300) if contract_points >= 100 else 50
        slam_bonus = 0
        if level == 6:
            slam_bonus = 750 if vulnerable else 500
        elif level == 7:
            slam_bonus = 1500 if vulnerable else 1000
        insult_bonus = 50 if multiplier == 2 else 100 if multiplier == 4 else 0
        declarer_score = (
            contract_points
            + overtrick_points
            + game_or_partscore_bonus
            + slam_bonus
            + insult_bonus
        )
    else:
        undertricks = -delta
        if multiplier == 1:
            penalty = undertricks * (100 if vulnerable else 50)
        elif vulnerable:
            penalty = 200 + max(0, undertricks - 1) * 300
        else:
            penalty = 100
            if undertricks >= 2:
                penalty += min(undertricks - 1, 2) * 200
            if undertricks >= 4:
                penalty += (undertricks - 3) * 300
        if multiplier == 4:
            penalty *= 2
        declarer_score = -penalty

    score_ns = declarer_score if seat in {"N", "S"} else -declarer_score
    return {
        "contract": f"{level}{strain}{doubling}",
        "declarer": seat,
        "vulnerability": vul,
        "declarer_vulnerable": vulnerable,
        "required_tricks": required,
        "dds3_tricks": tricks,
        "result_delta": delta,
        "made": delta >= 0,
        "score_declarer": declarer_score,
        "score_ns": score_ns,
    }


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AuctionScoringError(f"{label} must be an object")
    return dict(value)


def _sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def score_rollout_with_dds3(
    *,
    rollout: Any,
    dds3_results: Mapping[str, Any],
    vulnerability: Any,
) -> dict[str, Any]:
    """Bind completed BEN worlds to fingerprint-keyed real DDS3 table results."""
    source = _mapping(rollout, "BEN rollout")
    if not (
        source.get("engine") == "BEN"
        and source.get("fallback_used") is False
        and source.get("evidence_class") == "BEN_AUCTION_ROLLOUT"
        and source.get("complete") is True
        and source.get("dds_evaluated") is False
    ):
        raise AuctionScoringError("BEN rollout provenance is invalid")
    rows = source.get("worlds")
    if not isinstance(rows, list) or not rows:
        raise AuctionScoringError("BEN rollout has no completed worlds")
    if source.get("requested_worlds") != len(rows) or source.get("completed_worlds") != len(rows):
        raise AuctionScoringError("BEN rollout is incomplete")

    fingerprints = [str(row.get("world_fingerprint") or "") for row in rows if isinstance(row, Mapping)]
    if len(fingerprints) != len(rows) or any(not item for item in fingerprints):
        raise AuctionScoringError("BEN rollout world fingerprint is missing")
    if len(set(fingerprints)) != len(fingerprints):
        raise AuctionScoringError("BEN rollout contains duplicate worlds")

    vul = normalize_vulnerability(vulnerability)
    if "vulnerability" not in source:
        raise AuctionScoringError("BEN rollout vulnerability is missing")
    if normalize_vulnerability(source.get("vulnerability")) != vul:
        raise AuctionScoringError("BEN rollout vulnerability does not match scoring input")
    required_dds = {
        str(row["world_fingerprint"])
        for row in rows
        if isinstance(row, Mapping) and not row.get("passed_out")
    }
    if set(dds3_results) != required_dds:
        raise AuctionScoringError("DDS3 result set does not match non-passed-out worlds")

    scored: list[dict[str, Any]] = []
    for raw_row in rows:
        row = _mapping(raw_row, "BEN rollout world")
        fingerprint = str(row["world_fingerprint"])
        deal_pbn_sha256 = str(row.get("deal_pbn_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", deal_pbn_sha256):
            raise AuctionScoringError("BEN rollout deal PBN hash is invalid")
        if row.get("passed_out"):
            if row.get("contract") is not None or row.get("declarer") is not None:
                raise AuctionScoringError("passed-out world contains a contract")
            scored.append({
                "world_index": row.get("world_index"),
                "world_fingerprint": fingerprint,
                "deal_pbn_sha256": deal_pbn_sha256,
                "auction": row.get("auction"),
                "passed_out": True,
                "contract": None,
                "declarer": None,
                "dds3_evaluated": False,
                "score_ns": 0,
            })
            continue

        envelope = _mapping(dds3_results[fingerprint], "DDS3 result envelope")
        if (
            envelope.get("world_fingerprint") != fingerprint
            or envelope.get("deal_pbn_sha256") != deal_pbn_sha256
        ):
            raise AuctionScoringError("DDS3 result is not bound to the BEN deal")
        dds = _mapping(envelope.get("result"), "DDS3 result")
        if not (
            dds.get("engine") == "DDS3"
            and dds.get("fallback_used") is False
            and dds.get("operation") == "dd_table"
        ):
            raise AuctionScoringError("DDS3 provenance is invalid")
        hand_order = dds.get("hand_order")
        if hand_order != list(SEATS):
            raise AuctionScoringError("DDS3 hand order is invalid")
        table = _mapping(dds.get("dd_table"), "DDS3 table")
        level, strain, _ = _contract_parts(row.get("contract"))
        declarer = str(row.get("declarer") or "").upper()
        if declarer not in SEATS:
            raise AuctionScoringError("BEN rollout declarer is invalid")
        table_strain = "NT" if strain == "N" else strain
        values = table.get(table_strain)
        if not isinstance(values, list) or len(values) != 4:
            raise AuctionScoringError("DDS3 strain row is invalid")
        tricks = values[SEATS.index(declarer)]
        score = score_duplicate_contract(
            contract=row["contract"],
            declarer=declarer,
            tricks=tricks,
            vulnerability=vul,
        )
        scored.append({
            "world_index": row.get("world_index"),
            "world_fingerprint": fingerprint,
            "deal_pbn_sha256": deal_pbn_sha256,
            "auction": row.get("auction"),
            "passed_out": False,
            **score,
            "dds3_evaluated": True,
            "dds3_engine_version": dds.get("engine_version"),
            "dds3_sha256": _sha256(dds),
        })

    return {
        "engine": "BEN+DDS3+DUPLICATE_SCORING",
        "engines": {
            "auction": "BEN",
            "tricks": "DDS3",
            "scoring": "DUPLICATE_BRIDGE",
        },
        "fallback_used": False,
        "evidence_class": "BEN_AUCTION_ROLLOUT_WITH_DDS3_SCORING",
        "complete": True,
        "vulnerability": vul,
        "requested_worlds": len(rows),
        "completed_worlds": len(scored),
        "dds_required_worlds": len(required_dds),
        "dds_evaluated": bool(required_dds),
        "rollout_sha256": _sha256(source),
        "worlds": scored,
    }


__all__ = [
    "AuctionScoringError",
    "normalize_vulnerability",
    "score_duplicate_contract",
    "score_rollout_with_dds3",
]
