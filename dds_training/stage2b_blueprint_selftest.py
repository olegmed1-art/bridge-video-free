from __future__ import annotations

import json
import tempfile
from pathlib import Path

from stage2b_blueprint import write_multi_contract_blueprint

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks = root / "tasks.jsonl"
        out = root / "blueprint.jsonl"
        rows = [
            {
                "task_id": "BASE-10001-C",
                "deal_id": "DEAL-10001",
                "root_deal_id": "FAMILY-10001",
                "board": 10001,
                "split": "train",
                "task_type": "contract_tricks",
                "deal": DEAL,
                "declarer": 0,
                "strain": 4,
            },
            {
                "task_id": "BASE-10001-L",
                "deal_id": "DEAL-10001",
                "root_deal_id": "FAMILY-10001",
                "board": 10001,
                "split": "train",
                "task_type": "opening_lead",
                "deal": DEAL,
                "declarer": 0,
                "leader": 1,
                "strain": 4,
            },
            {
                "task_id": "OLD-9999-C",
                "deal_id": "DEAL-9999",
                "root_deal_id": "FAMILY-9999",
                "board": 9999,
                "split": "train",
                "task_type": "contract_tricks",
                "deal": DEAL,
                "declarer": 0,
                "strain": 4,
            },
        ]
        tasks.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        summary = write_multi_contract_blueprint(tasks, out, family_limit=1)
        generated = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert summary["families"] == 1
        assert summary["tasks"] == 20
        assert summary["cells_per_family"] == 20
        assert len(generated) == 20
        assert {(row["declarer"], row["strain"]) for row in generated} == {
            (declarer, strain) for declarer in range(4) for strain in range(5)
        }
        assert {row["root_deal_id"] for row in generated} == {"FAMILY-10001"}
        assert all(row["pre_dds_prediction_required"] is True for row in generated)
        assert all(row["status"] == "blueprint_not_authorized_for_dds" for row in generated)
        assert all(row["evidence_type"] == "reinforcement" for row in generated)
        assert summary["dds_called"] is False
        assert summary["predictions_locked"] is False
        print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
