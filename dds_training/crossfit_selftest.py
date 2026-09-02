from __future__ import annotations

import json
import tempfile
from pathlib import Path

from crossfit import annotate_file, audit_tasks, fold_for_family


def main() -> None:
    tasks = [
        {"task_id": "D1-CT", "deal_id": "D1", "split": "train", "task_type": "contract_tricks"},
        {"task_id": "D1-OL", "deal_id": "D1", "split": "train", "task_type": "opening_lead"},
        {
            "task_id": "D1-CT-ROT1", "deal_id": "D1-ROT1", "split": "derived",
            "source_root_split": "train", "source_root_deal_id": "D1", "task_type": "contract_tricks",
        },
        {"task_id": "D2-CT", "deal_id": "D2", "split": "validation", "task_type": "contract_tricks"},
    ]
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / "tasks.jsonl"
        target = Path(td) / "crossfit.jsonl"
        source.write_text("".join(json.dumps(x) + "\n" for x in tasks), encoding="utf-8")
        summary = annotate_file(source, target, folds=5, seed=17)
        rows = [json.loads(x) for x in target.read_text(encoding="utf-8").splitlines() if x.strip()]
        audit = audit_tasks(rows)
        assert audit["status"] == "ok", audit
        d1_folds = {x["crossfit_fold"] for x in rows if x["root_deal_id"] == "D1"}
        assert len(d1_folds) == 1
        assert summary["families"] == 2
        assert fold_for_family("D1", folds=5, seed=17) == next(iter(d1_folds))

        broken = [dict(x) for x in rows]
        broken[2]["crossfit_fold"] = (broken[2]["crossfit_fold"] + 1) % 5
        assert audit_tasks(broken)["status"] == "error"

    print(json.dumps({
        "ok": True,
        "same_family_same_fold": True,
        "derived_family_preserved": True,
        "leak_detection": True,
    }, indent=2))


if __name__ == "__main__":
    main()
