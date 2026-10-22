from __future__ import annotations

"""Deterministic deal-family folds for leakage-safe bridge learning."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path


def root_deal_id(task: dict) -> str:
    """Return an explicitly stored immutable family id when available."""
    roots = [task[key] for key in ("source_root_deal_id", "root_deal_id")
             if task.get(key) is not None]
    if any(type(value) is not str or not value.strip() or value != value.strip() for value in roots):
        raise ValueError("Root family IDs must be nonempty canonical strings")
    if len(set(roots)) > 1:
        raise ValueError(f"Conflicting explicit root families for {task.get('task_id')}")
    if roots:
        return roots[0]
    return str(task["deal_id"])


def _resolve_family(task: dict, by_task_id: dict[str, dict], seen: set[str] | None = None) -> str:
    """Follow derived_from_task_id until the original base deal is reached."""
    seen = set() if seen is None else set(seen)
    current_id = str(task.get("task_id", "<unknown>"))
    if current_id in seen:
        raise ValueError(f"Cycle in derived task ancestry at {current_id}")
    seen.add(current_id)
    declared = root_deal_id(task)
    source_id = task.get("derived_from_task_id")
    if source_id is None:
        return declared
    if type(source_id) is not str or not source_id.strip() or source_id != source_id.strip():
        raise ValueError(f"Invalid parent task ID for {current_id}")
    source = by_task_id.get(str(source_id))
    if source is None:
        raise ValueError(
            f"Cannot resolve root family for {current_id}: source task {source_id} is absent"
        )
    inherited = _resolve_family(source, by_task_id, seen)
    if (task.get("source_root_deal_id") is not None or task.get("root_deal_id") is not None) and declared != inherited:
        raise ValueError(f"Explicit family {declared} contradicts parent family {inherited} for {current_id}")
    return inherited


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

    # Resolve the complete graph before opening an output; an ancestry error
    # must not publish a partially annotated file.
    resolved = {str(task["task_id"]): _resolve_family(task, by_task_id) for task in rows}
    target.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    families: dict[str, int] = {}
    with target.open("w", encoding="utf-8") as out:
        for raw in rows:
            family = resolved[str(raw["task_id"])]
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
    ancestry_errors = []
    by_task_id = {}
    for task in tasks:
        task_id = task.get("task_id")
        if type(task_id) is not str or not task_id.strip() or task_id != task_id.strip() or task_id in by_task_id:
            ancestry_errors.append({"task_id": task_id, "reason": "Missing, invalid or duplicate task_id"})
        else:
            by_task_id[task_id] = task
    for task in tasks:
        if ("source_root_split" in task and task.get("split") != "derived"
                and task["source_root_split"] != task.get("split")):
            ancestry_errors.append({
                "task_id": task.get("task_id"),
                "reason": "source_root_split contradicts actual split on a non-derived task",
            })
        if "crossfit_fold" not in task or "root_deal_id" not in task:
            missing.append(task.get("task_id"))
            continue
        family = str(task["root_deal_id"])
        try:
            resolved = _resolve_family(task, by_task_id)
            if family != resolved:
                raise ValueError(f"Declared family {family} differs from resolved ancestry {resolved}")
        except (ValueError, KeyError, TypeError) as exc:
            ancestry_errors.append({"task_id": task.get("task_id"), "reason": str(exc)})
        family_folds[family].add(int(task["crossfit_fold"]))
        family_splits[family].add(str(task.get("source_root_split", task.get("split", "unknown"))))
    fold_leaks = {family: sorted(values) for family, values in family_folds.items() if len(values) != 1}
    split_leaks = {family: sorted(values) for family, values in family_splits.items() if len(values) != 1}
    return {
        "status": "ok" if not missing and not fold_leaks and not split_leaks and not ancestry_errors else "error",
        "tasks": len(tasks),
        "families": len(family_folds),
        "missing_crossfit_metadata": missing[:20],
        "family_fold_leaks": fold_leaks,
        "family_split_leaks": split_leaks,
        "ancestry_errors": ancestry_errors[:20],
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
