from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.recover_stage2c4_sealed_families import PROTOCOL, RecoveryError, recover


def _write(path: Path, rows: list[dict]) -> str:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    sealed_ids = sorted(str(row["task_id"]) for row in rows if row["split"] == "sealed_test")
    return hashlib.sha256("\n".join(sealed_ids).encode()).hexdigest()


def test_recovers_exact_historical_order_and_partition(tmp_path: Path):
    rows = []
    for split, count in (("train", 3), ("validation", 2), ("sealed_test", 7)):
        for index in range(count):
            rows.append({"task_id": f"{split}-{index}-CT", "root_deal_id": f"{split}-F{index}",
                         "split": split, "task_type": "contract_tricks"})
            if split == "sealed_test":
                rows.append({"task_id": f"{split}-{index}-AUX", "root_deal_id": f"{split}-F{index}",
                             "split": split, "task_type": "opening_lead"})
    path = tmp_path / "tasks.jsonl"
    digest = _write(path, rows)
    result = recover(path, expected_sealed_task_digest=digest, source_total=4)

    independently_ranked = sorted(
        ((hashlib.sha256(f"{PROTOCOL}:sealed_test-F{i}:sealed_test-{i}-CT".encode()).hexdigest(),
          f"sealed_test-F{i}") for i in range(7))
    )
    expected = sorted(family for _rank, family in independently_ranked[:4])
    assert result["manifests"]["stage2c4_selected"]["family_ids"] == expected
    assert result["manifests"]["all_sealed"]["count"] == 7
    assert result["manifests"]["remaining_unused"]["count"] == 3
    assert result["dds_called"] is False and result["results_read"] is False


def test_fails_closed_on_task_digest_mismatch(tmp_path: Path):
    path = tmp_path / "tasks.jsonl"
    _write(path, [{"task_id": "S-1", "deal_id": "F-1", "split": "sealed_test",
                   "task_type": "contract_tricks"}])
    with pytest.raises(RecoveryError, match="digest mismatch"):
        recover(path, expected_sealed_task_digest="0" * 64, source_total=1)


def test_fails_closed_on_cross_split_family_overlap(tmp_path: Path):
    path = tmp_path / "tasks.jsonl"
    rows = [
        {"task_id": "T-1", "deal_id": "SAME", "split": "train", "task_type": "contract_tricks"},
        {"task_id": "S-1", "deal_id": "SAME", "split": "sealed_test", "task_type": "contract_tricks"},
    ]
    digest = _write(path, rows)
    with pytest.raises(RecoveryError, match="overlap"):
        recover(path, expected_sealed_task_digest=digest, source_total=1)


def test_workflow_is_manual_identity_only_and_has_no_dds_execution():
    workflow = (Path(__file__).parents[1] / ".github/workflows/dds-stage2c4-family-recovery.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow and "schedule:" not in workflow
    assert "RECOVER_IDENTITIES_ONLY" in workflow
    assert "GOOGLE_DRIVE_OAUTH_JSON" in workflow
    assert "recover_stage2c4_sealed_families.py" in workflow
    assert "dds_stage2c4_sealed.py evaluate" not in workflow
    assert "bootstrap_linux.sh" not in workflow
    assert "oracle" not in workflow.lower()
