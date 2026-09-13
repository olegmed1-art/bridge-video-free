from __future__ import annotations

import json
from pathlib import Path

import pytest

from dds_training import oracle_mass_dispatch as mass


def test_path_must_stay_inside_mass_state_root(tmp_path: Path) -> None:
    root = tmp_path / "state"
    root.mkdir()
    inside = root / "work"
    inside.mkdir()
    assert mass._resolve_existing(str(inside), kind="work", allowed_root=root) == inside.resolve()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(SystemExit, match="escapes Oracle mass state root"):
        mass._resolve_existing(str(outside), kind="work", allowed_root=root)


def test_30k_requires_oracle_10k_pass(tmp_path: Path) -> None:
    root = tmp_path / "state"
    (root / "evidence").mkdir(parents=True)
    with pytest.raises(SystemExit, match="prior PASS evidence missing"):
        mass._require_prior_pass(root, 30_000)
    (root / "evidence" / "10000.json").write_text(
        json.dumps(
            {
                "schema": mass.EVIDENCE_SCHEMA,
                "target": 10_000,
                "status": "passed",
                "compute_plane": "oracle",
                "fallback_used": False,
            }
        ),
        encoding="utf-8",
    )
    mass._require_prior_pass(root, 30_000)


def test_prior_pass_rejects_non_oracle_compute(tmp_path: Path) -> None:
    root = tmp_path / "state"
    (root / "evidence").mkdir(parents=True)
    (root / "evidence" / "10000.json").write_text(
        json.dumps(
            {
                "schema": mass.EVIDENCE_SCHEMA,
                "target": 10_000,
                "status": "passed",
                "compute_plane": "github",
                "fallback_used": False,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit, match="not proven Oracle DDS3 compute"):
        mass._require_prior_pass(root, 30_000)


def test_fixed_stage_mapping_and_no_sealed_split(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    (repo / "dds_training").mkdir(parents=True)
    (repo / "dds_training" / "run_stage.py").write_text("pass\n", encoding="utf-8")
    work = state / "work" / "pilot"
    work.mkdir(parents=True)
    pred = work / "predictions.jsonl"
    pred.write_text("{}\n", encoding="utf-8")
    req = {
        "work_dir": str(work),
        "predictions_path": str(pred),
        "stage": "pilot",
        "splits": ["train"],
        "run_id": "oracle-10k-test",
    }
    argv, resolved_work = mass._build_run_stage_argv(repo, state, req, 10_000)
    assert resolved_work == work.resolve()
    assert argv[2:4] == ["evaluate", "--stage"]
    assert "10000" in argv
    assert "--start" in argv

    req["splits"] = ["sealed_test"]
    with pytest.raises(SystemExit, match="sealed_test"):
        mass._build_run_stage_argv(repo, state, req, 10_000)


def test_target_stage_mismatch_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    state = tmp_path / "state"
    (repo / "dds_training").mkdir(parents=True)
    (repo / "dds_training" / "run_stage.py").write_text("pass\n", encoding="utf-8")
    work = state / "work" / "main"
    work.mkdir(parents=True)
    pred = work / "predictions.jsonl"
    pred.write_text("{}\n", encoding="utf-8")
    req = {
        "work_dir": str(work),
        "predictions_path": str(pred),
        "stage": "main",
        "splits": ["train"],
        "run_id": "wrong-stage",
    }
    with pytest.raises(SystemExit, match="10k target must use pilot stage"):
        mass._build_run_stage_argv(repo, state, req, 10_000)
