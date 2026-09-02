from __future__ import annotations

import json

from confidence_calibration import apply_calibration, fit_calibrator


def make_rows(level: str, successes: int, failures: int) -> list[dict]:
    rows = []
    for index in range(successes + failures):
        exact = index < successes
        rows.append({
            "task_id": f"{level}-{index}",
            "task_type": "contract_tricks",
            "prediction": {"confidence": level, "tricks": 9},
            "result": {"dds_tricks": 9 if exact else 8},
            "out_of_fold": True,
        })
    return rows


def main() -> None:
    # Deliberately non-monotonic raw rates: low=.6, medium=.4, high=.8.
    rows = make_rows("low", 6, 4) + make_rows("medium", 4, 6) + make_rows("high", 8, 2)
    calibration = fit_calibrator(rows, minimum_count=5)
    mapping = calibration["families"]["contract_tricks"]["mapping"]
    low = mapping["low"]["calibrated_exact_probability"]
    medium = mapping["medium"]["calibrated_exact_probability"]
    high = mapping["high"]["calibrated_exact_probability"]
    assert low <= medium <= high
    assert abs(low - 0.5) < 1e-9
    assert abs(medium - 0.5) < 1e-9
    assert abs(high - 0.8) < 1e-9

    reviewed = apply_calibration(
        {"task_id": "X", "confidence": "medium", "locked": True},
        "contract_tricks",
        calibration,
        review_threshold=0.65,
    )
    assert reviewed["requires_human_or_deeper_review"] is True
    confident = apply_calibration(
        {"task_id": "Y", "confidence": "high", "locked": True},
        "contract_tricks",
        calibration,
        review_threshold=0.65,
    )
    assert confident["requires_human_or_deeper_review"] is False

    print(json.dumps({
        "ok": True,
        "monotonic": True,
        "low": low,
        "medium": medium,
        "high": high,
        "abstention_policy": True,
    }, indent=2))


if __name__ == "__main__":
    main()
