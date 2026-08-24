from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import oracle_mass_dispatch as base

PILOT_DEALS = 10_000
PILOT_TRAIN_TASKS = 14_000


def _count_jsonl(path: Path, *, split: str | None = None) -> int:
    n = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if split is None or row.get("split") == split:
                n += 1
    return n


def run_pilot(state_root: Path, repo_root: Path) -> int:
    target = PILOT_DEALS
    state_root = state_root.resolve()
    repo_root = repo_root.resolve()
    request_path = state_root / "requests" / "10000.json"
    if not request_path.is_file():
        raise SystemExit(f"FAIL_CLOSED: staged request missing: {request_path}")
    base._require_root_controlled(request_path)
    req = base._read_json(request_path)
    if req.get("schema") != base.REQUEST_SCHEMA or req.get("target") != target:
        raise SystemExit("FAIL_CLOSED: request schema/target mismatch")
    if req.get("compute_plane") != "oracle" or req.get("engine") != "DDS3" or req.get("fallback_used") is not False:
        raise SystemExit("FAIL_CLOSED: Oracle DDS3/no-fallback provenance required")
    if req.get("stage") != "pilot" or req.get("splits") != ["train"]:
        raise SystemExit("FAIL_CLOSED: Pilot-10k first compute phase must be stage=pilot, splits=[train]")

    corpus = base._resolve_existing(str(req.get("corpus_path", "")), kind="corpus", allowed_root=state_root)
    actual_sha = base._sha256(corpus)
    expected_sha = str(req.get("corpus_sha256", ""))
    if len(expected_sha) != 64 or actual_sha != expected_sha:
        raise SystemExit("FAIL_CLOSED: immutable corpus SHA-256 mismatch")

    work = base._resolve_existing(str(req.get("work_dir", "")), kind="work", allowed_root=state_root)
    tasks = base._resolve_existing(str(req.get("tasks_file", work / "blind_tasks.jsonl")), kind="tasks", allowed_root=state_root)
    predictions = base._resolve_existing(str(req.get("predictions_path", "")), kind="predictions", allowed_root=state_root)
    corpus_summary = base._read_json(work / "corpus_summary.json")
    if int(corpus_summary.get("count", -1)) != PILOT_DEALS or corpus_summary.get("raw_sha256") != actual_sha:
        raise SystemExit("FAIL_CLOSED: Pilot-10k corpus summary/count/hash mismatch")
    if _count_jsonl(tasks, split="train") != PILOT_TRAIN_TASKS:
        raise SystemExit("FAIL_CLOSED: Pilot-10k must contain exactly 14000 TRAIN tasks")
    if _count_jsonl(predictions) != PILOT_TRAIN_TASKS:
        raise SystemExit("FAIL_CLOSED: Pilot-10k requires exactly 14000 locked TRAIN predictions")

    run_id = str(req.get("run_id", "")).strip()
    if not run_id or len(run_id) > 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in run_id):
        raise SystemExit("FAIL_CLOSED: invalid run_id")

    script = repo_root / "dds_training" / "run_stage.py"
    argv = [
        sys.executable, str(script), "evaluate",
        "--stage", "pilot",
        "--work", str(work),
        "--predictions", str(predictions),
        "--splits", "train",
        "--run-id", run_id,
        "--tasks-file", str(tasks),
        "--start",
    ]
    evidence_path = state_root / "evidence" / "10000.json"
    log_path = state_root / "logs" / "10000.log"
    base_evidence = {
        "schema": base.EVIDENCE_SCHEMA,
        "target": target,
        "phase": "train",
        "status": "running",
        "authority": "EVIDENCE_ONLY",
        "compute_plane": "oracle",
        "compute_host": socket.gethostname(),
        "engine": "DDS3",
        "fallback_used": False,
        "corpus_sha256": actual_sha,
        "corpus_deals": PILOT_DEALS,
        "expected_train_tasks": PILOT_TRAIN_TASKS,
        "run_id": run_id,
        "stage_gate_ready_for_next_stage": False,
    }
    base._write_evidence(evidence_path, base_evidence)
    env = os.environ.copy()
    env["DDS_TRAINING_CONFIRM"] = "YES"
    env["BRIDGE_SCHOOL_COMPUTE_PLANE"] = "oracle"
    env["BRIDGE_SCHOOL_DDS3_FALLBACK_ALLOWED"] = "0"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.run(argv, cwd=str(repo_root / "dds_training"), env=env, stdout=log, stderr=subprocess.STDOUT, shell=False, check=False)
    final = dict(base_evidence)
    final["returncode"] = proc.returncode
    final["log_sha256"] = base._sha256(log_path)
    final["status"] = "train_passed" if proc.returncode == 0 else "failed"
    base._write_evidence(evidence_path, final)
    return proc.returncode


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=int, required=True, choices=base.ALLOWED_TARGETS)
    p.add_argument("--state-root", type=Path, default=Path(os.environ.get("DDS3_MASS_STATE_ROOT", base.DEFAULT_STATE_ROOT)))
    p.add_argument("--repo-root", type=Path, default=Path(os.environ.get("BRIDGE_SCHOOL_REPO_ROOT", "/opt/bridge-school/bridge-video-free")))
    args = p.parse_args()
    if args.target == PILOT_DEALS:
        return run_pilot(args.state_root, args.repo_root)
    raise SystemExit("FAIL_CLOSED: 30k/40k remain blocked until full Pilot-10k gate is completed")


if __name__ == "__main__":
    raise SystemExit(main())
