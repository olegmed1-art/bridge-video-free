from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.recover_stage2c4_sealed_families import recover
from tools.verify_stage2c4_family_recovery import VerificationError, verify


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    rows = []
    for split, count in (("train", 2), ("validation", 2), ("sealed_test", 9)):
        for family_index in range(count):
            for task_index in range(1 + family_index % 3):
                rows.append({
                    "task_id": f"{split}-{family_index}-{task_index}",
                    "root_deal_id": f"{split}-F{family_index}",
                    "split": split,
                    "task_type": "contract_tricks",
                })
    tasks = tmp_path / "tasks.jsonl"
    tasks.write_text("".join(json.dumps(row) + "\n" for row in reversed(rows)), encoding="utf-8")
    sealed_ids = sorted(row["task_id"] for row in rows if row["split"] == "sealed_test")
    digest = hashlib.sha256("\n".join(sealed_ids).encode()).hexdigest()
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(recover(tasks, expected_sealed_task_digest=digest, source_total=5)), encoding="utf-8")
    return tasks, evidence


def test_independent_verifier_accepts_exact_artifact(tmp_path: Path):
    tasks, evidence = _fixture(tmp_path)
    result = verify(tasks, evidence)
    assert result == {
        "status": "I2_PASS",
        "algorithm": "per-family-minimum-plus-heap",
        "selected": 5,
        "remaining": 4,
        "dds_called": False,
        "results_read": False,
    }


def test_independent_verifier_rejects_manifest_tampering(tmp_path: Path):
    tasks, evidence = _fixture(tmp_path)
    payload = json.loads(evidence.read_text())
    payload["manifests"]["stage2c4_selected"]["family_ids"].reverse()
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VerificationError, match="family IDs mismatch"):
        verify(tasks, evidence)
