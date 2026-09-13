"""Independently verify a Stage 2C.4 identity-recovery artifact."""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
from collections import defaultdict
from pathlib import Path


PROTOCOL = "dds-stage2c4-sealed-v1"


class VerificationError(ValueError):
    pass


def _digest(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()


def verify(tasks_path: Path, evidence_path: Path) -> dict:
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if evidence.get("identity_only") is not True:
        raise VerificationError("artifact is not identity-only")
    if evidence.get("dds_called") is not False or evidence.get("results_read") is not False:
        raise VerificationError("artifact reports DDS or result access")
    if evidence.get("historical_protocol") != PROTOCOL:
        raise VerificationError("historical protocol mismatch")

    split_families: dict[str, set[str]] = defaultdict(set)
    sealed_tasks: list[str] = []
    family_minima: dict[str, tuple[str, str]] = {}
    with tasks_path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                task_id = str(row["task_id"])
                split = str(row["split"])
                task_type = str(row["task_type"])
                family = str(row.get("root_deal_id") or row["deal_id"])
            except (KeyError, TypeError) as exc:
                raise VerificationError(f"invalid identity row {line_number}") from exc
            split_families[split].add(family)
            if split != "sealed_test":
                continue
            sealed_tasks.append(task_id)
            if task_type == "contract_tricks":
                rank = hashlib.sha256(f"{PROTOCOL}:{family}:{task_id}".encode("utf-8")).hexdigest()
                candidate = (rank, task_id)
                family_minima[family] = min(candidate, family_minima.get(family, candidate))

    sealed_tasks.sort()
    if _digest(sealed_tasks) != evidence.get("sealed_task_id_digest"):
        raise VerificationError("sealed task digest mismatch")
    if any(
        split_families[left] & split_families[right]
        for left, right in (("train", "validation"), ("train", "sealed_test"), ("validation", "sealed_test"))
    ):
        raise VerificationError("cross-split family overlap")

    source_total = int(evidence["source_total"])
    selected = sorted(
        family
        for family, _rank in heapq.nsmallest(
            source_total, family_minima.items(), key=lambda item: item[1]
        )
    )
    all_sealed = sorted(split_families["sealed_test"])
    remaining = sorted(set(all_sealed) - set(selected))
    expected = {
        "all_sealed": all_sealed,
        "stage2c4_selected": selected,
        "remaining_unused": remaining,
    }
    for name, family_ids in expected.items():
        manifest = evidence["manifests"][name]
        if manifest.get("family_ids") != family_ids:
            raise VerificationError(f"{name} family IDs mismatch")
        if manifest.get("count") != len(family_ids) or manifest.get("sha256") != _digest(family_ids):
            raise VerificationError(f"{name} count/hash mismatch")

    return {
        "status": "I2_PASS",
        "algorithm": "per-family-minimum-plus-heap",
        "selected": len(selected),
        "remaining": len(remaining),
        "dds_called": False,
        "results_read": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.tasks, args.evidence), sort_keys=True))


if __name__ == "__main__":
    main()
