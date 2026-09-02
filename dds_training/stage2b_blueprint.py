from __future__ import annotations

import json
from pathlib import Path


def write_multi_contract_blueprint(tasks_path: Path, out_path: Path, family_limit: int = 500) -> dict:
    rows = []
    families = set()
    for line in tasks_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        task = json.loads(line)
        if task.get("task_type") != "contract_tricks" or task.get("split") != "train" or int(task.get("board", 0)) < 10001:
            continue
        family = str(task.get("root_deal_id", task["deal_id"]))
        if family not in families and len(families) >= family_limit:
            continue
        families.add(family)
        for declarer in range(4):
            for strain in range(5):
                task_id = f"{family}-GRID-{declarer}-{strain}"
                rows.append({
                    "task_id": task_id,
                    "deal_id": task["deal_id"],
                    "root_deal_id": family,
                    "split": "derived",
                    "source_root_split": "train",
                    "task_type": "contract_tricks",
                    "declarer": declarer,
                    "strain": strain,
                    "deal": task["deal"],
                    "evidence_type": "reinforcement",
                    "family_preserving": True,
                    "pre_dds_prediction_required": True,
                    "status": "blueprint_not_authorized_for_dds",
                })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return {"schema": "dds-multi-contract-blueprint-v1", "families": len(families), "tasks": len(rows), "cells_per_family": 20, "dds_called": False, "predictions_locked": False, "path": str(out_path)}
