from __future__ import annotations

"""Deterministic deal-family folds for leakage-safe bridge learning."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def root_deal_id(task: dict) -> str:
    """Return an explicitly stored immutable family id when available."""
    value = task.get("source_root_deal_id") or task.get("root_deal_id")
    if value:
        return str(value)
    return str(task["deal_id"])


def _resolve_family(task: dict, by_task_id: dict[str, dict], seen: set[str] | None = None) -> str:
    """Follow derived_from_task_id until the original base deal is reached."""
    explicit = task.get("source_root_deal_id") or task.get("root_deal_id")
    if explicit:
        return str(explicit)
    source_id = task.get("derived_from_task_id")
    if not source_id:
        return str(task["deal_id"])
    seen = set() if seen is None else set(seen)
    current_id = str(task.get("task_id", "<unknown>"))
    if current_id in seen:
        raise ValueError(f"Cycle in derived task ancestry at {current_id}")
    seen.add(current_id)
    source = by_task_id.get(str(source_id))
    if source is None:
        raise ValueError(
            f"Cannot resolve root family for {current_id}: source task {source_id} is absent"
        )
    return _resolve_family(source, by_task_id, seen)


def fold_for_family(family_id: str, *, folds: int, seed: int) -> int:
    if folds < 2:
        raise ValueError("folds must be >= 2")
    digest = hashlib.sha256(f"{seed}:{family_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % folds


def annotate_task(task: dict, *, folds: int, seed: int, family_id: str | None = None) -> dict:
    out = dict(task)
    family = root_deal_id(task) if family_id is None else str(family_id)
    out["root_deal_id"] = family
    out["crossfit_fold"] = fold_for_family(family, folds=folds, seed=seed)
    out["crossfit_folds"] = folds
    out["crossfit_seed"] = seed
    return out


def annotate_file(source: Path, target: Path, *, folds: int, seed: int) -> dict:
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_task_id = {str(task["task_id"]): task for task in rows}
    if len(by_task_id) != len(rows):
        raise ValueError("Duplicate task_id in cross-fit source")

    target.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    families: dict[str, int] = {}
    with target.open("w", encoding="utf-8") as out:
        for raw in rows:
            family = _resolve_family(raw, by_task_id)
            task = annotate_task(raw, folds=folds, seed=seed, family_id=family)
            fold = int(task["crossfit_fold"])
            if family in families and families[family] != fold:
                raise ValueError(f"Family {family} was assigned to multiple folds")
            families[family] = fold
            counts[fold] += 1
            out.write(json.dumps(task, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "tasks": len(rows),
        "families": len(families),
        "folds": folds,
        "seed": seed,
        "tasks_by_fold": {str(k): int(v) for k, v in sorted(counts.items())},
        "path": str(target),
    }


def audit_tasks(tasks: list[dict]) -> dict:
    family_folds: dict[str, set[int]] = defaultdict(set)
    family_splits: dict[str, set[str]] = defaultdict(set)
    missing = []
    for task in tasks:
        if "crossfit_fold" not in task or "root_deal_id" not in task:
            missing.append(task.get("task_id"))
            continue
        family = str(task["root_deal_id"])
        family_folds[family].add(int(task["crossfit_fold"]))
        family_splits[family].add(str(task.get("source_root_split", task.get("split", "unknown"))))
    fold_leaks = {family: sorted(values) for family, values in family_folds.items() if len(values) != 1}
    split_leaks = {family: sorted(values) for family, values in family_splits.items() if len(values) != 1}
    return {
        "status": "ok" if not missing and not fold_leaks and not split_leaks else "error",
        "tasks": len(tasks),
        "families": len(family_folds),
        "missing_crossfit_metadata": missing[:20],
        "family_fold_leaks": fold_leaks,
        "family_split_leaks": split_leaks,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Annotate JSONL tasks with family-safe cross-fit folds")
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260815)
    args = parser.parse_args()
    summary = annotate_file(Path(args.tasks), Path(args.out), folds=args.folds, seed=args.seed)
    rows = [json.loads(x) for x in Path(args.out).read_text(encoding="utf-8").splitlines() if x.strip()]
    audit = audit_tasks(rows)
    print(json.dumps({"summary": summary, "audit": audit}, ensure_ascii=False, indent=2))
    if audit["status"] != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
