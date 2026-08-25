from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import oracle_mass_dispatch as base
from oracle_cost_gate import CostGateError, validate_30k_budget

TARGET = 30_000
TRAIN_TASKS = 28_000
SCOPE = "main_train"


def _rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise SystemExit("FAIL_CLOSED: JSONL row must be an object")
                rows.append(row)
    return rows


def validate_train_identity(tasks_path: Path, predictions_path: Path) -> int:
    tasks = _rows(tasks_path)
    predictions = _rows(predictions_path)
    train = [row for row in tasks if row.get("split") == "train"]
    if len(tasks) != TRAIN_TASKS or len(train) != TRAIN_TASKS:
        raise SystemExit("FAIL_CLOSED: 30k operator requires exactly 28000 TRAIN-only tasks")
    task_ids = [str(row.get("task_id", "")) for row in train]
    prediction_ids = [str(row.get("task_id", "")) for row in predictions]
    if not all(task_ids) or len(set(task_ids)) != TRAIN_TASKS:
        raise SystemExit("FAIL_CLOSED: TRAIN task IDs must be present and unique")
    if len(prediction_ids) != TRAIN_TASKS or len(set(prediction_ids)) != TRAIN_TASKS:
        raise SystemExit("FAIL_CLOSED: locked prediction IDs must be present and unique")
    if set(task_ids) != set(prediction_ids):
        raise SystemExit("FAIL_CLOSED: TRAIN task and locked prediction identity mismatch")
    if not all(row.get("locked") is True for row in predictions):
        raise SystemExit("FAIL_CLOSED: every 30k prediction must be locked before DDS")
    return TRAIN_TASKS


def validate_pilot_gate(work: Path, state_root: Path) -> None:
    summary = base._read_json(work / "pilot_final_summary.json")
    gate = summary.get("stage_gate")
    if (
        summary.get("stage") != "pilot"
        or summary.get("deals") != 10_000
        or summary.get("audit", {}).get("status") != "ok"
        or not isinstance(gate, dict)
        or gate.get("ready_for_next_stage") is not True
        or gate.get("open_mandatory_investigations") != 0
    ):
        raise SystemExit("FAIL_CLOSED: preserved full Pilot-10k gate is not PASS")
    evidence = base._read_json(state_root / "evidence" / "10000.json")
    if (
        evidence.get("schema") != base.EVIDENCE_SCHEMA
        or evidence.get("target") != 10_000
        or evidence.get("status") not in {"train_passed", "passed"}
        or evidence.get("compute_plane") != "oracle"
        or evidence.get("engine") != "DDS3"
        or evidence.get("fallback_used") is not False
    ):
        raise SystemExit("FAIL_CLOSED: live Oracle Pilot-10k TRAIN evidence is not PASS")


def _authorization(req: dict[str, Any], state_root: Path) -> tuple[Path, str, dict[str, str]]:
    auth = req.get("authorization")
    if not isinstance(auth, dict) or auth.get("scope") != SCOPE:
        raise SystemExit("FAIL_CLOSED: 30k requires one-time main_train authorization")
    if auth.get("event_name") != "workflow_dispatch" or auth.get("command"):
        raise SystemExit("FAIL_CLOSED: 30k authorization must be workflow_dispatch without issue command")
    if auth.get("actor") != "olegmed1-art" or auth.get("triggering_actor") != "olegmed1-art":
        raise SystemExit("FAIL_CLOSED: 30k authorization actor mismatch")
    receipt = base._resolve_existing(
        str(auth.get("receipt_path", "")), kind="authorization receipt", allowed_root=state_root
    )
    nonce = str(auth.get("nonce", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", nonce):
        raise SystemExit("FAIL_CLOSED: invalid 30k authorization nonce")
    env = {
        "GITHUB_REPOSITORY": str(auth.get("repository", "")),
        "GITHUB_REF_NAME": str(auth.get("ref_name", "")),
        "GITHUB_SHA": str(auth.get("commit_sha", "")),
        "GITHUB_ACTOR": str(auth.get("actor", "")),
        "GITHUB_TRIGGERING_ACTOR": str(auth.get("triggering_actor", "")),
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "DDS_AUTHORIZATION_COMMAND": "",
    }
    if env["GITHUB_REPOSITORY"] != "olegmed1-art/bridge-video-free":
        raise SystemExit("FAIL_CLOSED: 30k authorization repository mismatch")
    if not re.fullmatch(r"[0-9a-f]{40}", env["GITHUB_SHA"]):
        raise SystemExit("FAIL_CLOSED: invalid authorized runtime commit")
    return receipt, nonce, env


def run_main(state_root: Path, repo_root: Path) -> int:
    state_root = state_root.resolve()
    repo_root = repo_root.resolve()
    request_path = state_root / "requests" / "30000.json"
    if not request_path.is_file():
        raise SystemExit("FAIL_CLOSED: staged 30000 request is missing")
    base._require_root_controlled(request_path)
    req = base._read_json(request_path)
    if req.get("schema") != base.REQUEST_SCHEMA or req.get("target") != TARGET:
        raise SystemExit("FAIL_CLOSED: 30k request schema/target mismatch")
    if (
        req.get("compute_plane") != "oracle"
        or req.get("engine") != "DDS3"
        or req.get("fallback_used") is not False
        or req.get("stage") != "main"
        or req.get("splits") != ["train"]
    ):
        raise SystemExit("FAIL_CLOSED: 30k is Oracle DDS3 main TRAIN-only with no fallback")

    try:
        budget = validate_30k_budget(req.get("budget"))
    except CostGateError as exc:
        raise SystemExit("FAIL_CLOSED: %s" % exc) from exc

    corpus = base._resolve_existing(str(req.get("corpus_path", "")), kind="corpus", allowed_root=state_root)
    actual_sha = base._sha256(corpus)
    if actual_sha != str(req.get("corpus_sha256", "")):
        raise SystemExit("FAIL_CLOSED: immutable 30k corpus SHA-256 mismatch")
    work = base._resolve_existing(str(req.get("work_dir", "")), kind="work", allowed_root=state_root)
    corpus_summary = base._read_json(work / "corpus_summary.json")
    if corpus_summary.get("count") != TARGET or corpus_summary.get("raw_sha256") != actual_sha:
        raise SystemExit("FAIL_CLOSED: 30k corpus summary/count/hash mismatch")
    validate_pilot_gate(work, state_root)

    tasks = base._resolve_existing(str(req.get("tasks_file", "")), kind="tasks", allowed_root=state_root)
    predictions = base._resolve_existing(
        str(req.get("predictions_path", "")), kind="predictions", allowed_root=state_root
    )
    validated_tasks = validate_train_identity(tasks, predictions)
    receipt, nonce, auth_env = _authorization(req, state_root)
    run_id = str(req.get("run_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", run_id):
        raise SystemExit("FAIL_CLOSED: invalid 30k run_id")

    wrapper = repo_root / "dds_training" / "authorized_run_stage.py"
    if not wrapper.is_file():
        raise SystemExit("FAIL_CLOSED: authorized wrapper is missing")
    argv = [
        sys.executable, str(wrapper),
        "--receipt", str(receipt), "--nonce", nonce,
        "--scope", SCOPE, "--manifest", str(tasks),
        "--consume-dir", str(state_root / "authorization-consumed"),
        "--stage", "main", "--work", str(work),
        "--predictions", str(predictions), "--tasks-file", str(tasks),
        "--run-id", run_id, "--no-generate-followups",
    ]
    evidence_path = state_root / "evidence" / "30000.json"
    log_path = state_root / "logs" / "30000.log"
    evidence = {
        "schema": base.EVIDENCE_SCHEMA,
        "target": TARGET,
        "phase": "train",
        "status": "running",
        "authority": "ONE_TIME_AUTHORIZED_WRAPPER",
        "compute_plane": "oracle",
        "compute_host": socket.gethostname(),
        "engine": "DDS3",
        "fallback_used": False,
        "splits": ["train"],
        "validation_opened": False,
        "sealed_test_opened": False,
        "corpus_sha256": actual_sha,
        "expected_train_tasks": validated_tasks,
        "run_id": run_id,
        "budget": {
            "max_runtime_seconds": budget.max_runtime_seconds,
            "max_cost_usd": budget.max_cost_usd,
            "estimated_ceiling_usd": budget.estimated_ceiling_usd,
            "hourly_retail_usd": budget.hourly_retail_usd,
        },
    }
    base._write_evidence(evidence_path, evidence)
    env = os.environ.copy()
    env.update(auth_env)
    env["BRIDGE_SCHOOL_COMPUTE_PLANE"] = "oracle"
    env["BRIDGE_SCHOOL_DDS3_FALLBACK_ALLOWED"] = "0"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with log_path.open("ab") as log:
        proc = subprocess.run(
            argv, cwd=str(repo_root / "dds_training"), env=env,
            stdout=log, stderr=subprocess.STDOUT, shell=False, check=False,
            timeout=budget.max_runtime_seconds,
        )
    elapsed = round(time.monotonic() - started, 3)
    final = dict(evidence)
    final.update({
        "returncode": proc.returncode,
        "wall_elapsed_seconds": elapsed,
        "estimated_retail_cost_usd": round(elapsed / 3600 * budget.hourly_retail_usd, 6),
        "log_sha256": base._sha256(log_path),
        "status": "train_passed" if proc.returncode == 0 else "failed",
    })
    base._write_evidence(evidence_path, final)
    return proc.returncode
