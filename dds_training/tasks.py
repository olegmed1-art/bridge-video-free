from __future__ import annotations

import hashlib
import json
from pathlib import Path

from config import STRAINS
from corpus import iter_pbn_records


def _stable_int(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


def create_blind_tasks(raw_pbn: Path, manifest_jsonl: Path, out_jsonl: Path) -> dict:
    manifest = {}
    for line in manifest_jsonl.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        manifest[row["deal_id"]] = row

    total = 0
    by_type = {"contract_tricks": 0, "opening_lead": 0}
    with out_jsonl.open("w", encoding="utf-8") as out:
        for rec in iter_pbn_records(raw_pbn):
            meta = manifest[rec["deal_id"]]
            x = _stable_int(rec["deal_id"])
            declarer = x % 4  # N=0,E=1,S=2,W=3
            strain = (x // 4) % 5  # S,H,D,C,NT

            common = {
                "deal_id": rec["deal_id"],
                "board": int(meta["board"]),
                "dealer": meta["dealer"],
                "vulnerability": meta["vulnerability"],
                "split": meta["split"],
                "deal": rec["deal"],
                "declarer": declarer,
                "strain": strain,
                "strain_name": STRAINS[strain],
                "blind": True,
            }

            # Two independent technical tasks per deal: one declarer-side value
            # estimate and one defense opening-lead choice.
            tasks = [
                {
                    **common,
                    "task_id": f"{rec['deal_id']}-CT",
                    "task_type": "contract_tricks",
                    "prediction_schema": {
                        "tricks": "integer 0..13",
                        "confidence": "low|medium|high",
                        "reason": "short bridge explanation",
                        "line": "optional list of planned cards",
                    },
                },
                {
                    **common,
                    "task_id": f"{rec['deal_id']}-OL",
                    "task_type": "opening_lead",
                    "leader": (declarer + 1) % 4,
                    "prediction_schema": {
                        "card": "SHDC + rank, e.g. S7 or HA",
                        "expected_defense_tricks": "optional integer 0..13",
                        "confidence": "low|medium|high",
                        "reason": "short bridge explanation",
                        "line": "optional list of planned defense cards",
                    },
                },
            ]
            for task in tasks:
                out.write(json.dumps(task, ensure_ascii=False) + "\n")
                total += 1
                by_type[task["task_type"]] += 1

    return {"tasks": total, "by_type": by_type, "path": str(out_jsonl)}


def load_locked_predictions(path: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        task_id = row.get("task_id")
        if not task_id:
            raise ValueError(f"Prediction line {line_no}: missing task_id")
        if not row.get("locked", False):
            raise ValueError(f"Prediction {task_id} is not locked")
        if task_id in result:
            raise ValueError(f"Duplicate prediction for {task_id}")
        result[task_id] = row
    return result
