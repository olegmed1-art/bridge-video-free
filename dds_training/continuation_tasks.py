from __future__ import annotations

"""Create blind mid-play decision tasks from a legal real or predicted line."""

import argparse
import hashlib
import json
from pathlib import Path

from playline import replay_line


def continuation_tasks_from_line(
    source_task: dict,
    cards: list[str],
    *,
    prefix_indexes: list[int] | None = None,
    include_declarer: bool = True,
    include_defense: bool = True,
    provenance: str = "predicted_line",
) -> list[dict]:
    first = int(source_task.get("leader", (int(source_task["declarer"]) + 1) % 4))
    replay = replay_line(
        deal=source_task["deal"],
        declarer=int(source_task["declarer"]),
        trump=int(source_task["strain"]),
        opening_leader=first,
        cards=cards,
    )
    if prefix_indexes is None:
        # Include decisions immediately after the lead and at every completed
        # trick boundary, excluding the terminal prefix with no remaining cards.
        prefix_indexes = [1]
        prefix_indexes.extend(
            index for index, snapshot in enumerate(replay["snapshots"][1:], 1)
            if snapshot["completed_tricks"] > 0 and not snapshot["current_trick"]
        )
    selected = sorted({int(x) for x in prefix_indexes if 0 <= int(x) < len(replay["snapshots"])})

    out = []
    for prefix in selected:
        snapshot = replay["snapshots"][prefix]
        actor = str(snapshot["next_actor"])
        if actor == "declarer" and not include_declarer:
            continue
        if actor == "defense" and not include_defense:
            continue
        prefix_cards = [item["card"] for item in replay["played"][:prefix]]
        task_type = f"{actor}_continuation"
        identity = hashlib.sha256(
            f"{source_task['task_id']}:{prefix}:{' '.join(prefix_cards)}".encode("utf-8")
        ).hexdigest()[:16]
        out.append({
            "task_id": f"{source_task['task_id']}-{task_type.upper()}-{prefix:02d}-{identity}",
            "deal_id": source_task["deal_id"],
            "root_deal_id": source_task.get("root_deal_id", source_task["deal_id"]),
            "derived_from_task_id": source_task["task_id"],
            "source_root_split": source_task.get("source_root_split", source_task.get("split")),
            "split": "derived",
            "task_type": task_type,
            "actor": actor,
            "blind": True,
            "deal": source_task["deal"],
            "declarer": int(source_task["declarer"]),
            "strain": int(source_task["strain"]),
            "leader": first,
            "play_prefix": prefix_cards,
            "prefix_cards": prefix,
            "position_id": snapshot["position_id"],
            "remaining_deal": snapshot["remaining_deal"],
            "current_trick": snapshot["current_trick"],
            "next_seat": snapshot["next_seat"],
            "completed_tricks": snapshot["completed_tricks"],
            "declarer_tricks": snapshot["declarer_tricks"],
            "defense_tricks": snapshot["defense_tricks"],
            "line_provenance": provenance,
            "evidence_type": "transfer" if provenance == "real_play" else "reinforcement",
            "prediction_schema": {
                "card": "legal next card, e.g. S7 or HA",
                "confidence": "low|medium|high",
                "reason": "bridge explanation based on the current position",
                "continuation_plan": "optional further legal card sequence",
                "locked": True,
            },
        })
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Create blind continuation tasks from line-bearing JSONL")
    parser.add_argument("--input", required=True, help="JSONL rows containing task and line")
    parser.add_argument("--out", required=True)
    parser.add_argument("--provenance", choices=("predicted_line", "real_play"), default="predicted_line")
    args = parser.parse_args()
    tasks = []
    for line in Path(args.input).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        source = row.get("task", row)
        cards = row.get("line") or row.get("play") or row.get("cards")
        if not isinstance(cards, list):
            raise ValueError("Each row must contain a list field line/play/cards")
        tasks.extend(continuation_tasks_from_line(source, cards, provenance=args.provenance))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for task in tasks:
            handle.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps({
        "tasks": len(tasks),
        "declarer": sum(x["actor"] == "declarer" for x in tasks),
        "defense": sum(x["actor"] == "defense" for x in tasks),
        "provenance": args.provenance,
        "dds_called": False,
        "path": str(out),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
