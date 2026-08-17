from __future__ import annotations

import json

from shard_plan import build_shard_plan


def main() -> None:
    tasks = []
    for family in range(7):
        for suffix, task_type in (("CT", "contract_tricks"), ("OL", "opening_lead")):
            tasks.append({
                "task_id": f"D{family}-{suffix}",
                "root_deal_id": f"D{family}",
                "crossfit_fold": family % 3,
                "split": "train",
                "task_type": task_type,
            })
    first = build_shard_plan(tasks, stage="main", max_tasks=5, selected_splits={"train"})
    second = build_shard_plan(list(reversed(tasks)), stage="main", max_tasks=5, selected_splits={"train"})
    assert first["all_task_ids_sha256"] == second["all_task_ids_sha256"]
    assert first["task_count"] == 14
    assert first["family_count"] == 7
    assert first["family_safe"] is True
    assert all(shard["task_count"] <= 5 for shard in first["shards"])

    family_shard = {}
    for shard in first["shards"]:
        for task_id in shard["task_ids"]:
            family = task_id.split("-")[0]
            family_shard.setdefault(family, shard["shard_id"])
            assert family_shard[family] == shard["shard_id"]

    print(json.dumps({
        "ok": True,
        "tasks": first["task_count"],
        "families": first["family_count"],
        "shards": first["shard_count"],
        "family_safe": True,
        "deterministic": True,
    }, indent=2))


if __name__ == "__main__":
    main()
