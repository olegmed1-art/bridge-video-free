"""Independent, stdlib-only checks for the bounded IBM deal-world workload.

This module deliberately does not import the world generator, its parsers, or
its fingerprint helpers. It validates the *reported* attempt count; it cannot
prove an execution history from a result file. The golden hash was observed on
CPython 3.12 and is not a promise of determinism across Python versions.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import platform
from typing import Any


INPUT = {
    "known_seat": "N",
    "known_hand_pbn": "AKQJ.T98.765.432",
    "constraints": {"seats": {"S": {"hcp": [12, 14], "suits": {"H": [5, 5]}}}},
    "count": 256,
    "seed": 20261011,
    "max_attempts": 50000,
}
EXPECTED_RESULT_SHA256 = "9dc92edb3d3f5b8816f06349a85ccc1e60fb4c6472dff6a9b6bfe95a8e6e8121"
EXPECTED_ATTEMPTS = 8836
GENERATOR_SOURCE_SHA256 = "c43b595ded92ee74da5ac884737585b0bfcc5b97a3c689afe126079ca04f884d"
REFERENCE_PYTHON = "CPython 3.12"


class ValidationError(ValueError):
    """A result is not the exact approved, complete workload."""


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical artifact encoding: sorted, compact, ASCII JSON plus one LF."""
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")


INPUT_BYTES = canonical_json_bytes(INPUT)
INPUT_CANONICAL_BYTES = INPUT_BYTES
INPUT_SHA256 = hashlib.sha256(INPUT_BYTES).hexdigest()
# Generator fingerprints exclude the artifact's trailing newline.
CONSTRAINTS_SHA256 = hashlib.sha256(canonical_json_bytes(INPUT["constraints"])[:-1]).hexdigest()

_SEATS = ("N", "E", "S", "W")
_SUITS = "SHDC"
_RANKS = "AKQJT98765432"
_DECK = frozenset(suit + rank for suit in _SUITS for rank in _RANKS)
_KNOWN_NORTH = "AKQJ.T98.765.432"
_TOP_KEYS = frozenset({
    "generator", "generator_version", "seed", "requested", "attempts", "accepted",
    "complete", "constraint_class", "constraints_sha256", "engine", "fallback_used", "worlds",
})
_WORLD_KEYS = frozenset({"world_index", "hands", "pbn", "fingerprint"})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _object(value: Any, keys: frozenset[str], label: str) -> None:
    _require(type(value) is dict, f"{label} must be an object")
    _require(set(value) == keys, f"{label} has missing or unexpected fields")


def _exact(value: Any, expected: Any, label: str) -> None:
    _require(type(value) is type(expected) and value == expected,
             f"{label} must equal {expected!r} with the exact type")


def _read_hand(pbn: Any, label: str) -> tuple[frozenset[str], int, tuple[int, ...]]:
    _require(type(pbn) is str, f"{label} must be a PBN string")
    parts = pbn.split(".")
    _require(len(parts) == 4, f"{label} must have four suits")
    cards = []
    hcp = 0
    for suit, ranks in zip(_SUITS, parts):
        _require(all(rank in _RANKS for rank in ranks), f"{label} has an invalid rank")
        _require(len(set(ranks)) == len(ranks), f"{label} has duplicate cards")
        _require(ranks == "".join(rank for rank in _RANKS if rank in ranks),
                 f"{label} ranks must be in canonical descending order")
        cards.extend(suit + rank for rank in ranks)
        # Independent direct count, not the generator's card/HCP helper.
        hcp += 4 * ranks.count("A") + 3 * ranks.count("K") + 2 * ranks.count("Q") + ranks.count("J")
    _require(len(cards) == 13, f"{label} must contain exactly 13 cards")
    return frozenset(cards), hcp, tuple(len(part) for part in parts)


def validate_result(result: Any, *, expected_result_sha256: str | None = None) -> dict[str, Any]:
    """Check exact schema, all bridge invariants, and independent fingerprints.

    By default a golden-hash difference is reported, not hidden. A caller that
    needs byte-identical reference output must pass EXPECTED_RESULT_SHA256 as
    expected_result_sha256. Every mode still enforces the approved workload and
    its reported 8,836 attempts. No result metadata is trusted as validation.
    """
    _object(result, _TOP_KEYS, "result")
    expected_metadata = {
        "generator": "explicit-constraint-deal-sampler",
        "generator_version": "v1",
        "seed": 20261011,
        "requested": 256,
        "accepted": 256,
        "complete": True,
        "constraint_class": "EXPLICIT_HARD_CONSTRAINTS",
        "constraints_sha256": CONSTRAINTS_SHA256,
        "engine": "WORLD_GENERATOR",
        "fallback_used": False,
    }
    for key, expected in expected_metadata.items():
        _exact(result[key], expected, key)
    attempts = result["attempts"]
    _require(type(attempts) is int and 256 <= attempts <= 50000,
             "attempts must be an integer within the approved bound [256, 50000]")
    _exact(attempts, EXPECTED_ATTEMPTS, "attempts")
    worlds = result["worlds"]
    _require(type(worlds) is list and len(worlds) == 256, "worlds must be a list of exactly 256 worlds")

    seen_deals: set[tuple[str, ...]] = set()
    hcp_counts: dict[str, Counter[int]] = {seat: Counter() for seat in _SEATS}
    south_shapes: Counter[str] = Counter()
    for index, world in enumerate(worlds):
        label = f"worlds[{index}]"
        _object(world, _WORLD_KEYS, label)
        _exact(world["world_index"], index, f"{label}.world_index")
        hands = world["hands"]
        _object(hands, frozenset(_SEATS), f"{label}.hands")
        deal_cards: set[str] = set()
        hand_details = {}
        for seat in _SEATS:
            cards, hcp, shape = _read_hand(hands[seat], f"{label}.hands.{seat}")
            _require(not deal_cards.intersection(cards), f"{label} has duplicate cards between hands")
            deal_cards.update(cards)
            hand_details[seat] = (hcp, shape)
            hcp_counts[seat][hcp] += 1
        _require(deal_cards == _DECK, f"{label} must contain the entire 52-card deck exactly once")
        _exact(hands["N"], _KNOWN_NORTH, f"{label}.hands.N")
        south_hcp, south_shape = hand_details["S"]
        _require(12 <= south_hcp <= 14, f"{label}: South HCP must be in [12, 14]")
        _require(south_shape[1] == 5, f"{label}: South must have exactly five hearts")
        _require(hand_details["N"][1][1] + south_shape[1] == 8,
                 f"{label}: North-South must have an eight-card heart fit")
        _require(sum(details[0] for details in hand_details.values()) == 40,
                 f"{label}: total HCP must be 40")
        south_shapes["-".join(str(length) for length in south_shape)] += 1

        pbn = "N:" + " ".join(hands[seat] for seat in _SEATS)
        _exact(world["pbn"], pbn, f"{label}.pbn")
        fingerprint = hashlib.sha256(canonical_json_bytes(hands)[:-1]).hexdigest()
        _exact(world["fingerprint"], fingerprint, f"{label}.fingerprint")
        deal_identity = tuple(hands[seat] for seat in _SEATS)
        _require(deal_identity not in seen_deals, f"{label} duplicates an earlier deal")
        seen_deals.add(deal_identity)

    result_bytes = canonical_json_bytes(result)
    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    if expected_result_sha256 is not None:
        _require(type(expected_result_sha256) is str and len(expected_result_sha256) == 64
                 and all(character in "0123456789abcdef" for character in expected_result_sha256),
                 "expected_result_sha256 must be a lowercase SHA-256 digest")
        _exact(result_sha256, expected_result_sha256, "result SHA-256")
    return {
        "validator": "ibm-worlds-independent-v1",
        "valid": True,
        "input_sha256": INPUT_SHA256,
        "constraints_sha256": CONSTRAINTS_SHA256,
        "result_sha256": result_sha256,
        "result_bytes": len(result_bytes),
        "requested": 256,
        "accepted": len(worlds),
        "distinct_worlds": len(seen_deals),
        "complete": True,
        "attempts": attempts,
        "max_attempts": 50000,
        "engine": result["engine"],
        "fallback_used": False,
        "known_north_hand_pbn": _KNOWN_NORTH,
        "north_south_heart_fit": 8,
        "hcp_distribution_by_seat": {
            seat: {str(hcp): count for hcp, count in sorted(counts.items())}
            for seat, counts in hcp_counts.items()
        },
        "south_shape_distribution_shdc": dict(sorted(south_shapes.items())),
        "reference_result_sha256": EXPECTED_RESULT_SHA256,
        "matches_reference_result": result_sha256 == EXPECTED_RESULT_SHA256,
        "reference_python": REFERENCE_PYTHON,
        "cross_version_determinism": "not_established",
    }


def validate_repeated_results(
    first: Any,
    second: Any,
    *,
    generation_python_version: str,
    expected_result_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate two supplied runs; generation version is explicitly caller-reported.

    A same-version repeat establishes only these two observations. The current
    validator runtime is collected separately so it cannot silently masquerade
    as the generator's runtime when validating artifacts from another machine.
    """
    _require(type(generation_python_version) is str and bool(generation_python_version.strip()),
             "generation_python_version must identify the observed generation runtime")
    verification = validate_result(first, expected_result_sha256=expected_result_sha256)
    validate_result(second, expected_result_sha256=expected_result_sha256)
    _require(canonical_json_bytes(first) == canonical_json_bytes(second),
             "two generated runs are not byte-identical")
    verification["repeatability"] = {
        "runs_checked": 2,
        "identical_canonical_bytes": True,
        "generation_python_version_reported": generation_python_version,
        "validator_python_version": platform.python_version(),
        "validator_python_implementation": platform.python_implementation(),
        "scope": "these two supplied runs only; cross-version determinism is not established",
    }
    return verification


def render_pbn(result: Any) -> bytes:
    """Return inert ASCII PBN deal records only after complete validation."""
    validate_result(result)
    records = ["% PBN 2.1\n% EXPORT\n"]
    for world in result["worlds"]:
        records.append(f'[Board "{world["world_index"] + 1}"]\n[Deal "{world["pbn"]}"]\n')
    return ("\n".join(records) + "\n").encode("ascii")


__all__ = [
    "INPUT", "INPUT_BYTES", "INPUT_CANONICAL_BYTES", "INPUT_SHA256", "CONSTRAINTS_SHA256",
    "EXPECTED_ATTEMPTS", "EXPECTED_RESULT_SHA256", "GENERATOR_SOURCE_SHA256", "REFERENCE_PYTHON",
    "ValidationError", "canonical_json_bytes", "validate_result", "validate_repeated_results", "render_pbn",
]
