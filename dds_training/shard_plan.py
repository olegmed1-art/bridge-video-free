from __future__ import annotations

"""Deterministic restartable shard manifests for large DDS stages."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def _canonical_ids(ids: list[str]) -> str:
    return "\n".join(ids) + "\n"


def _sha(ids: list[str]) -> str:
    return hashlib.sha256(_canonical_ids(ids).encode("utf-8")).hexdigest()


def build_shard_plan(
    tasks: list[dict],
    *,
    stage: str,
    max_tasks: int,
    selected_splits: set[str] | None = None,
) -> dict:
    if max_tasks < 1:
        raise ValueError("max_tasks must be positive")
    selected_splits = selected_splits or {str(x.get("split")) for x in tasks}
    selected = [x for x in tasks if str(x.get("split")) in selected_splits]
    if not selected:
        raise ValueError("No tasks selected for shard plan")

    by_id: dict[str, dict] = {}
    families: dict[str, list[dict]] = defaultdict(list)
    missing_family = []
    for task in selected:
        task_id = str(task["task_id"])
        if task_id in by_id:
            raise ValueError(f"Duplicate task_id before sharding: {task_id}")
        by_id[task_id] = task
        family = task.get("root_deal_id")
        if not family:
            missing_family.append(task_id)
            continue
        families[str(family)].append(task)
    if missing_family:
        raise ValueError(
            f"Tasks lack root_deal_id; run crossfit annotation first: {missing_family[:5]}"
        )

    ordered_families = sorted(
        families,
        key=lambda family: (
            min(str(t.get("split", "")) for t in families[family]),
            int(families[family][0].get("crossfit_fold", -1)),
            family,
        ),
    )

    shards: list[list[dict]] = []
    current: list[dict] = []
    for family in ordered_families:
        family_tasks = sorted(families[family], key=lambda x: str(x["task_id"]))
        if len(family_tasks) > max_tasks:
            raise ValueError(
                f"One family {family} has {len(family_tasks)} tasks, exceeding max_tasks={max_tasks}"
            )
        if current and len(current) + len(family_tasks) > max_tasks:
            shards.append(current)
            current = []
        current.extend(family_tasks)
    if current:
        shards.append(current)

    manifest_shards = []
    all_ids: list[str] = []
    family_to_shards: dict[str, set[int]] = defaultdict(set)
    for index, shard in enumerate(shards, 1):
        ids = [str(x["task_id"]) for x in shard]
        all_ids.extend(ids)
        split_counts = Counter(str(x.get("split")) for x in shard)
        type_counts = Counter(str(x.get("task_type")) for x in shard)
        fold_counts = Counter(str(x.get("crossfit_fold")) for x in shard)
        family_ids = sorted({str(x["root_deal_id"]) for x in shard})
        for family in family_ids:
            family_to_shards[family].add(index)
        shard_id = f"{stage}-shard-{index:04d}"
        ids_sha = _sha(ids)
        manifest_shards.append({
            "shard_id": shard_id,
            "index": index,
            "task_count": len(ids),
            "family_count": len(family_ids),
            "task_ids_sha256": ids_sha,
            "task_ids": ids,
            "first_task_id": ids[0],
            "last_task_id": ids[-1],
            "splits": dict(sorted(split_counts.items())),
            "task_types": dict(sorted(type_counts.items())),
            "crossfit_folds": dict(sorted(fold_counts.items())),
            "expected_result_artifact": f"{shard_id}-state.tgz",
            "resume_key": hashlib.sha256(
                f"{stage}:{index}:{ids_sha}".encode("utf-8")
            ).hexdigest()[:24],
        })

    if len(set(all_ids)) != len(all_ids):
        raise ValueError("Duplicate task ids across shard plan")
    if set(all_ids) != set(by_id):
        raise ValueError("Shard plan does not cover exactly the selected tasks")

    split_families = {k: sorted(v) for k, v in family_to_shards.items() if len(v) != 1}
    if split_families:
        raise ValueError(f"Families split across shards: {split_families}")

    return {
        "schema": "dds-shard-plan-v1",
        "stage": stage,
        "selected_splits": sorted(selected_splits),
        "max_tasks_per_shard": max_tasks,
        "task_count": len(selected),
        "family_count": len(families),
        "shard_count": len(manifest_shards),
        "all_task_ids_sha256": _sha(sorted(all_ids)),
        "shards": manifest_shards,
        "family_safe": True,
        "restartable": True,
    }


def write_shards(tasks: list[dict], plan: dict, out_dir: Path) -> dict:
    by_id = {str(x["task_id"]): x for x in tasks}
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for shard in plan["shards"]:
        path = out_dir / f"{shard['shard_id']}.jsonl"
        with path.open("w", encoding="utf-8") as handle:
            for task_id in shard["task_ids"]:
                handle.write(json.dumps(by_id[task_id], ensure_ascii=False, sort_keys=True) + "\n")
        written.append({
            "shard_id": shard["shard_id"],
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "task_count": shard["task_count"],
        })
    return {"written": written, "count": len(written)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create deterministic family-safe DDS shard manifests")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--shard-dir")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--max-tasks", type=int, default=2000)
    parser.add_argument("--splits", nargs="+")
    args = parser.parse_args()

    tasks = [
        json.loads(line)
        for line in Path(args.tasks).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    plan = build_shard_plan(
        tasks,
        stage=args.stage,
        max_tasks=args.max_tasks,
        selected_splits=None if not args.splits else set(args.splits),
    )
    Path(args.out).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    output = {"plan": args.out, "summary": {k: v for k, v in plan.items() if k != "shards"}}
    if args.shard_dir:
        output["files"] = write_shards(tasks, plan, Path(args.shard_dir))
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
