from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

import oracle_mass_dispatch as base

PILOT_DEALS = 10_000
PILOT_TRAIN_TASKS = 14_000
PILOT_SCOPE = "pilot_train"
PILOT_COMMAND = "/dds3-pilot10k start"


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


def _safe_log_tail(path: Path, *, lines: int = 40, max_chars: int = 6000) -> list[str]:
    """Return a bounded, sanitized diagnostic tail suitable for evidence."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError as exc:
        return [f"log_tail_unavailable:{type(exc).__name__}"]
    safe: list[str] = []
    patterns = (
        (re.compile(r"postgres(?:ql)?://\S+", re.I), "[REDACTED_DSN]"),
        (re.compile(r"Bearer\s+\S+", re.I), "Bearer [REDACTED]"),
        (re.compile(r"(?i)(token|password|secret|api[_-]?key|dsn)=\S+"), r"\1=[REDACTED]"),
    )
    used = 0
    for line in raw:
        text = line
        for pattern, replacement in patterns:
            text = pattern.sub(replacement, text)
        remaining = max_chars - used
        if remaining <= 0:
            break
        text = text[:remaining]
        safe.append(text)
        used += len(text) + 1
    return safe


def _authorization(req: dict, state_root: Path) -> tuple[Path, str, dict[str, str]]:
    auth = req.get("authorization")
    if not isinstance(auth, dict):
        raise SystemExit("FAIL_CLOSED: Pilot-10k requires one-time launch authorization")
    if auth.get("scope") != PILOT_SCOPE:
        raise SystemExit("FAIL_CLOSED: Pilot-10k authorization scope must be pilot_train")
    if auth.get("event_name") != "issue_comment" or auth.get("command") != PILOT_COMMAND:
        raise SystemExit("FAIL_CLOSED: Pilot-10k authorization must come from the exact owner start command")
    if auth.get("actor") != "olegmed1-art" or auth.get("triggering_actor") != "olegmed1-art":
        raise SystemExit("FAIL_CLOSED: Pilot-10k authorization actor mismatch")
    receipt = base._resolve_existing(str(auth.get("receipt_path", "")), kind="authorization receipt", allowed_root=state_root)
    nonce = str(auth.get("nonce", ""))
    if len(nonce) < 32 or len(nonce) > 128 or not re.fullmatch(r"[A-Za-z0-9_-]+", nonce):
        raise SystemExit("FAIL_CLOSED: invalid Pilot-10k authorization nonce")
    metadata = {
        "GITHUB_REPOSITORY": str(auth.get("repository", "")),
        "GITHUB_REF_NAME": str(auth.get("ref_name", "")),
        "GITHUB_SHA": str(auth.get("commit_sha", "")),
        "GITHUB_ACTOR": str(auth.get("actor", "")),
        "GITHUB_TRIGGERING_ACTOR": str(auth.get("triggering_actor", "")),
        "GITHUB_EVENT_NAME": str(auth.get("event_name", "")),
        "DDS_AUTHORIZATION_COMMAND": str(auth.get("command", "")),
    }
    if not metadata["GITHUB_REPOSITORY"] or not metadata["GITHUB_REF_NAME"]:
        raise SystemExit("FAIL_CLOSED: incomplete Pilot-10k authorization metadata")
    if not re.fullmatch(r"[0-9a-f]{40}", metadata["GITHUB_SHA"]):
        raise SystemExit("FAIL_CLOSED: invalid authorized runtime commit")
    return receipt, nonce, metadata


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

    receipt, nonce, auth_env = _authorization(req, state_root)
    wrapper = repo_root / "dds_training" / "authorized_run_stage.py"
    if not wrapper.is_file():
        raise SystemExit("FAIL_CLOSED: authorized_run_stage.py is missing")
    consume_dir = state_root / "authorization-consumed"
    argv = [
        sys.executable, str(wrapper),
        "--receipt", str(receipt),
        "--nonce", nonce,
        "--scope", PILOT_SCOPE,
        "--manifest", str(tasks),
        "--consume-dir", str(consume_dir),
        "--stage", "pilot",
        "--work", str(work),
        "--predictions", str(predictions),
        "--run-id", run_id,
        "--tasks-file", str(tasks),
        "--no-generate-followups",
    ]
    evidence_path = state_root / "evidence" / "10000.json"
    log_path = state_root / "logs" / "10000.log"
    base_evidence = {
        "schema": base.EVIDENCE_SCHEMA,
        "target": target,
        "phase": "train",
        "status": "running",
        "authority": "ONE_TIME_AUTHORIZED_WRAPPER",
        "authorization_event": auth_env["GITHUB_EVENT_NAME"],
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
    env.update(auth_env)
    env["BRIDGE_SCHOOL_COMPUTE_PLANE"] = "oracle"
    env["BRIDGE_SCHOOL_DDS3_FALLBACK_ALLOWED"] = "0"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        proc = subprocess.run(argv, cwd=str(repo_root / "dds_training"), env=env, stdout=log, stderr=subprocess.STDOUT, shell=False, check=False)
    final = dict(base_evidence)
    final["returncode"] = proc.returncode
    final["log_sha256"] = base._sha256(log_path)
    final["status"] = "train_passed" if proc.returncode == 0 else "failed"
    if proc.returncode != 0:
        final["failure_tail"] = _safe_log_tail(log_path)
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
