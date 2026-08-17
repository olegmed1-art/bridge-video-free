from __future__ import annotations

import json
import tempfile
from pathlib import Path

import baseline_predictor as bp

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    contract = {
        "task_id": "BASE-CT",
        "task_type": "contract_tricks",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
    }
    lead = {
        "task_id": "BASE-OL",
        "task_type": "opening_lead",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "leader": 3,
    }

    first = bp.prediction_for(contract, "baseline-selftest")
    second = bp.prediction_for(contract, "baseline-selftest")
    assert first == second
    assert 0 <= first["tricks"] <= 13
    assert first["locked"] is True
    assert first["line"] == []
    assert "no DDS" in first["reason"]

    lead_prediction = bp.prediction_for(lead, "baseline-selftest")
    assert lead_prediction["locked"] is True
    assert lead_prediction["line"] == [lead_prediction["card"]]
    hands = bp.parse_deal(DEAL)
    card = lead_prediction["card"]
    suit = bp.SUITS.index(card[0])
    assert card[1:] in hands[lead["leader"]][suit]

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks = root / "tasks.jsonl"
        out = root / "predictions.jsonl"
        rows = [contract, lead, {**contract, "task_id": "VALID", "split": "validation"}]
        tasks.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        summary = bp.generate(tasks, out, {"train"}, "baseline-selftest")
        generated = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert summary["predictions"] == 2, summary
        assert summary["dds_called"] is False
        assert {row["task_id"] for row in generated} == {"BASE-CT", "BASE-OL"}
        assert all(row["locked"] is True for row in generated)

    try:
        bp.prediction_for({**contract, "task_type": "unsupported"}, "baseline-selftest")
    except ValueError as exc:
        assert "Unsupported" in str(exc)
    else:
        raise AssertionError("Unsupported task type was accepted")

    print(
        json.dumps(
            {
                "ok": True,
                "deterministic": True,
                "contract_prediction_bounded": True,
                "opening_lead_owned_by_leader": True,
                "split_filter_tested": True,
                "dds_called": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
