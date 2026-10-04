"""Independent finite recommendation-policy oracle; offline and standard-library only.

Run: python -m experiments.tournament_teacher.independent_review

The expected choices below come from the confirmed teacher instruction, not
rules.json, predicate helpers, or the existing scenario expectations. Exhaustive
means all suit distributions at the enumerated point premises and auctions;
it does not mean all card ranks, all point values, or a complete bidding system.
"""
from collections import Counter
from copy import deepcopy
from hashlib import sha256
from itertools import product
import json
from pathlib import Path

from .consumer import decide


AUCTIONS = (
    (),
    ("1D", "PASS", "1NT", "PASS"),
    ("1S", "PASS"),
    ("1H", "PASS", "1NT", "PASS"),
    ("1S", "PASS", "1NT", "PASS"),
    ("1C", "PASS", "1H", "PASS"),
    ("1D", "PASS", "1H", "PASS", "1S", "PASS"),
    ("1D", "PASS", "1H", "PASS", "1NT", "PASS"),
    ("2S", "PASS"),
    ("1NT", "PASS"),
)
POINT_PREMISES = (0, 8, 12, 14, 15, 16, 17, 18, 20, 22, 23, 41)


def expected_choice(shape, points, auction):
    """Only the teacher's explicit 15-17, 5M332 choice can recommend."""
    if points not in (15, 16, 17):
        return None
    # A separate constructive shape definition avoids the consumer's sorted
    # distribution predicate and does not inspect executable rule metadata.
    for index, major in ((0, "S"), (1, "H")):
        others = [length for i, length in enumerate(shape) if i != index]
        if shape[index] != 5 or others.count(2) != 1 or others.count(3) != 2:
            continue
        if auction == ():
            return "1" + major
        if auction == ("1" + major, "PASS", "1NT", "PASS"):
            return "2NT"
    return None


def review_recommendations():
    counts = Counter()
    distributions = 0
    for spades, hearts, diamonds in product(range(14), repeat=3):
        clubs = 13 - spades - hearts - diamonds
        if not 0 <= clubs <= 13:
            continue
        distributions += 1
        shape = (spades, hearts, diamonds, clubs)
        hand = [suit + rank for suit, length in zip("SHDC", shape)
                for rank in "23456789TJQKA"[:length]]
        for points in POINT_PREMISES:
            for auction in AUCTIONS:
                request = {
                    "mode": "TOURNAMENT_TEACHER_TEST_ONLY",
                    "system_profile": "SCHOOL_TOURNAMENT_CURRENT_V1",
                    "task": "recommend", "dealer": "N",
                    "actor": "NESW"[len(auction) % 4],
                    "auction": list(auction), "hand": hand,
                    "school_points": {
                        "value": points, "basis": "SYNTHETIC_SCHOOL_POINTS",
                    },
                }
                if auction == ("2S", "PASS"):
                    request["opening_meaning"] = "WEAK_2S"
                result = decide(request, enabled=True)
                expected = expected_choice(shape, points, auction)
                expected_status = "RECOMMEND" if expected is not None else "ABSTAIN"
                observed = (result["status"], result["action"])
                if observed != (expected_status, expected):
                    raise AssertionError({
                        "shape": shape, "points": points, "auction": auction,
                        "expected": (expected_status, expected), "observed": observed,
                    })
                for flag in ("formal_db_activation_asserted", "production_ready",
                             "fallback_performed"):
                    if result[flag] is not False:
                        raise AssertionError({"unexpected_flag": flag})
                counts[result["status"]] += 1
    assert distributions == 560
    assert dict(counts) == {"ABSTAIN": 67164, "RECOMMEND": 36}
    return {"distributions": distributions, "point_premises": len(POINT_PREMISES),
            "auctions": len(AUCTIONS), "decisions": sum(counts.values()),
            "statuses": dict(counts), "policy_anomalies": 0}


def review_malformed_fields():
    """Robustness probe only: reuses request fixtures, not semantic expectations."""
    from .scenarios import CASES, make_request

    values = [None, False, True, 0, -1, 15.0, "", [], {}, [{}],
              {"value": [], "basis": "SYNTHETIC_SCHOOL_POINTS"}]
    probes = 0
    for case in CASES:
        base = make_request(case)
        for key in base:
            for value in values:
                request = deepcopy(base)
                request[key] = value
                result = decide(request, enabled=True)
                assert result["action"] is None
                assert result["formal_db_activation_asserted"] is False
                probes += 1
    assert probes == 2574
    return {"probes": probes, "crashes": 0}


def main():
    root = Path(__file__).parent
    evidence = {
        "recommendation_policy": review_recommendations(),
        "malformed_fields": review_malformed_fields(),
        "sha256": {name: sha256((root / name).read_bytes()).hexdigest()
                   for name in ("consumer.py", "rules.json", "independent_review.py")},
    }
    print(json.dumps(evidence, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
