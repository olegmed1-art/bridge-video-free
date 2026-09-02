"""Recover the historical Stage 2C.4 sealed family selection from identities only."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Mapping


PROTOCOL = "dds-stage2c4-sealed-v1"
SCHEMA = "dds-stage2c4-sealed-family-recovery-v1"


class RecoveryError(ValueError):
    """Raised when identity evidence is incomplete or inconsistent."""


def _digest_lines(values: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RecoveryError(f"invalid JSONL at line {line_number}") from exc
            if not isinstance(row, dict):
                raise RecoveryError(f"non-object JSONL row at line {line_number}")
            yield row


def recover(
    tasks_path: Path,
    *,
    expected_sealed_task_digest: str,
    source_total: int = 650,
) -> dict:
    if not tasks_path.is_file() or tasks_path.is_symlink():
        raise RecoveryError("tasks path must be a regular non-symlink file")
    if len(expected_sealed_task_digest) != 64:
        raise RecoveryError("expected sealed task digest must be SHA-256")
    if type(source_total) is not int or source_total <= 0:
        raise RecoveryError("source_total must be positive")

    split_counts: Counter[str] = Counter()
    families: dict[str, set[str]] = defaultdict(set)
    sealed_task_ids: list[str] = []
    sealed_contracts: list[tuple[str, str]] = []
    for row in _read_jsonl(tasks_path):
        required = {"task_id", "split", "task_type"}
        if not required.issubset(row):
            raise RecoveryError("task identity fields are missing")
        task_id = str(row["task_id"])
        split = str(row["split"])
        family = str(row.get("root_deal_id") or row.get("deal_id") or "")
        if not task_id or not family:
            raise RecoveryError("blank task or family identity")
        split_counts[split] += 1
        families[split].add(family)
        if split == "sealed_test":
            sealed_task_ids.append(task_id)
            if row["task_type"] == "contract_tricks":
                sealed_contracts.append((family, task_id))

    sealed_task_ids.sort()
    observed_task_digest = _digest_lines(sealed_task_ids)
    if observed_task_digest != expected_sealed_task_digest:
        raise RecoveryError("sealed task identity digest mismatch")

    overlaps = {
        "train_validation": len(families["train"] & families["validation"]),
        "train_sealed": len(families["train"] & families["sealed_test"]),
        "validation_sealed": len(families["validation"] & families["sealed_test"]),
    }
    if any(overlaps.values()):
        raise RecoveryError(f"family overlap across root splits: {overlaps}")

    sealed_contracts.sort(
        key=lambda item: (
            hashlib.sha256(f"{PROTOCOL}:{item[0]}:{item[1]}".encode("utf-8")).hexdigest(),
            item[1],
        )
    )
    selected: list[str] = []
    selected_set: set[str] = set()
    for family, _task_id in sealed_contracts:
        if family in selected_set:
            continue
        selected.append(family)
        selected_set.add(family)
        if len(selected) == source_total:
            break
    if len(selected) != source_total:
        raise RecoveryError(f"insufficient unique sealed contract families: {len(selected)}")

    all_sealed = sorted(families["sealed_test"])
    selected_sorted = sorted(selected_set)
    remaining = sorted(families["sealed_test"] - selected_set)
    if selected_set | set(remaining) != set(all_sealed) or selected_set & set(remaining):
        raise RecoveryError("selected/remaining partition proof failed")

    return {
        "schema": SCHEMA,
        "status": "RECOVERED",
        "identity_only": True,
        "dds_called": False,
        "results_read": False,
        "historical_protocol": PROTOCOL,
        "selection_rule": "SHA-256(protocol:family:task_id), task_id tie-break, first unique families",
        "source_total": source_total,
        "split_task_counts": dict(sorted(split_counts.items())),
        "split_family_counts": {key: len(value) for key, value in sorted(families.items())},
        "split_family_overlap": overlaps,
        "sealed_task_count": len(sealed_task_ids),
        "sealed_task_id_digest": observed_task_digest,
        "manifests": {
            "all_sealed": {"count": len(all_sealed), "sha256": _digest_lines(all_sealed), "family_ids": all_sealed},
            "stage2c4_selected": {"count": len(selected_sorted), "sha256": _digest_lines(selected_sorted), "family_ids": selected_sorted},
            "remaining_unused": {"count": len(remaining), "sha256": _digest_lines(remaining), "family_ids": remaining},
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--expected-sealed-task-digest", required=True)
    parser.add_argument("--source-total", type=int, default=650)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = recover(
        args.tasks,
        expected_sealed_task_digest=args.expected_sealed_task_digest,
        source_total=args.source_total,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("status", "identity_only", "dds_called", "results_read")}, sort_keys=True))


if __name__ == "__main__":
    main()
