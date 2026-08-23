from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

ALLOWED_TARGETS = (10_000, 30_000, 40_000)
REQUEST_SCHEMA = "bridge-school-dds3-mass-request/v1"
EVIDENCE_SCHEMA = "bridge-school-dds3-mass-evidence/v1"
DEFAULT_STATE_ROOT = Path("/opt/bridge-school/dds3-mass-validation")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"FAIL_CLOSED: expected JSON object: {path}")
    return data


def _require_root_controlled(path: Path) -> None:
    st = path.stat()
    if st.st_uid != 0 or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise SystemExit(f"FAIL_CLOSED: request is not root-controlled: {path}")


def _resolve_existing(path_value: str, *, kind: str, allowed_root: Path) -> Path:
    p = Path(path_value)
    if not p.is_absolute():
        raise SystemExit(f"FAIL_CLOSED: {kind} path must be absolute")
    p = p.resolve()
    try:
        p.relative_to(allowed_root.resolve())
    except ValueError as exc:
        raise SystemExit(f"FAIL_CLOSED: {kind} path escapes Oracle mass state root") from exc
    if kind == "work":
        if not p.is_dir():
            raise SystemExit(f"FAIL_CLOSED: work directory missing: {p}")
    elif not p.is_file():
        raise SystemExit(f"FAIL_CLOSED: {kind} file missing: {p}")
    return p


def _require_prior_pass(state_root: Path, target: int) -> None:
    if target == 10_000:
        return
    expected = 10_000 if target == 30_000 else 30_000
    path = state_root / "evidence" / f"{expected}.json"
    if not path.is_file():
        raise SystemExit(f"FAIL_CLOSED: prior PASS evidence missing: {path}")
    ev = _read_json(path)
    if ev.get("schema") != EVIDENCE_SCHEMA or ev.get("target") != expected or ev.get("status") != "passed":
        raise SystemExit("FAIL_CLOSED: prior-stage evidence is not a valid PASS")
    if ev.get("compute_plane") != "oracle" or ev.get("fallback_used") is not False:
        raise SystemExit("FAIL_CLOSED: prior stage was not proven Oracle DDS3 compute")


def _build_run_stage_argv(
    repo_root: Path,
    state_root: Path,
    req: dict[str, Any],
    target: int,
) -> tuple[list[str], Path]:
    work = _resolve_existing(str(req.get("work_dir", "")), kind="work", allowed_root=state_root)
    predictions = _resolve_existing(
        str(req.get("predictions_path", "")), kind="predictions", allowed_root=state_root
    )
    stage = str(req.get("stage", ""))
    if stage not in {"pilot", "main"}:
        raise SystemExit("FAIL_CLOSED: stage must be pilot or main")
    if target == 10_000 and stage != "pilot":
        raise SystemExit("FAIL_CLOSED: 10k target must use pilot stage")
    if target in {30_000, 40_000} and stage != "main":
        raise SystemExit("FAIL_CLOSED: 30k/40k targets must use main stage")

    splits = req.get("splits")
    if not isinstance(splits, list) or not splits or not all(x in {"train", "validation"} for x in splits):
        raise SystemExit(
            "FAIL_CLOSED: splits must be a non-empty train/validation list; sealed_test is never opened by mass dispatch"
        )
    run_id = str(req.get("run_id", "")).strip()
    if not run_id or len(run_id) > 128 or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for c in run_id
    ):
        raise SystemExit("FAIL_CLOSED: invalid run_id")

    script = (repo_root / "dds_training" / "run_stage.py").resolve()
    try:
        script.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise SystemExit("FAIL_CLOSED: run_stage.py escaped repository root") from exc
    if not script.is_file():
        raise SystemExit("FAIL_CLOSED: verified run_stage.py missing")

    argv = [
        sys.executable,
        str(script),
        "evaluate",
        "--stage",
        stage,
        "--work",
        str(work),
        "--predictions",
        str(predictions),
        "--splits",
        *splits,
        "--run-id",
        run_id,
        "--limit",
        str(target),
        "--start",
    ]
    tasks_file = req.get("tasks_file")
    if tasks_file:
        tasks = _resolve_existing(str(tasks_file), kind="tasks", allowed_root=state_root)
        argv += ["--tasks-file", str(tasks)]
    return argv, work


def _write_evidence(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def run(target: int, state_root: Path, repo_root: Path) -> int:
    if target not in ALLOWED_TARGETS:
        raise SystemExit("FAIL_CLOSED: target must be 10000, 30000, or 40000")
    state_root = state_root.resolve()
    repo_root = repo_root.resolve()
    request_path = state_root / "requests" / f"{target}.json"
    if not request_path.is_file():
        raise SystemExit(f"FAIL_CLOSED: staged request missing: {request_path}")
    _require_root_controlled(request_path)
    req = _read_json(request_path)
    if req.get("schema") != REQUEST_SCHEMA or req.get("target") != target:
        raise SystemExit("FAIL_CLOSED: request schema/target mismatch")
    if req.get("compute_plane") != "oracle":
        raise SystemExit("FAIL_CLOSED: compute_plane must be oracle")
    if req.get("engine") != "DDS3" or req.get("fallback_used") is not False:
        raise SystemExit("FAIL_CLOSED: request must require engine=DDS3 and fallback_used=false")

    corpus = _resolve_existing(
        str(req.get("corpus_path", "")), kind="corpus", allowed_root=state_root
    )
    expected_sha = str(req.get("corpus_sha256", ""))
    actual_sha = _sha256(corpus)
    if len(expected_sha) != 64 or expected_sha != actual_sha:
        raise SystemExit("FAIL_CLOSED: immutable corpus SHA-256 mismatch")

    _require_prior_pass(state_root, target)
    argv, work = _build_run_stage_argv(repo_root, state_root, req, target)
    evidence_path = state_root / "evidence" / f"{target}.json"
    log_path = state_root / "logs" / f"{target}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    base = {
        "schema": EVIDENCE_SCHEMA,
        "target": target,
        "status": "running",
        "authority": "EVIDENCE_ONLY",
        "compute_plane": "oracle",
        "compute_host": socket.gethostname(),
        "engine": "DDS3",
        "fallback_used": False,
        "corpus_path": str(corpus),
        "corpus_sha256": actual_sha,
        "repo_root": str(repo_root),
        "run_id": req["run_id"],
        "work_dir": str(work),
        "forbidden_promotions": ["canon", "curriculum", "methodology", "mastery", "student_profile"],
    }
    _write_evidence(evidence_path, base)

    env = os.environ.copy()
    env["DDS_TRAINING_CONFIRM"] = "YES"
    env["BRIDGE_SCHOOL_COMPUTE_PLANE"] = "oracle"
    env["BRIDGE_SCHOOL_DDS3_FALLBACK_ALLOWED"] = "0"
    with log_path.open("ab") as log:
        proc = subprocess.run(
            argv,
            cwd=str(repo_root / "dds_training"),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            shell=False,
            check=False,
        )
    final = dict(base)
    final["returncode"] = proc.returncode
    final["log_sha256"] = _sha256(log_path)
    final["status"] = "passed" if proc.returncode == 0 else "failed"
    _write_evidence(evidence_path, final)
    return proc.returncode


def main() -> int:
    p = argparse.ArgumentParser(description="Bounded Oracle-only DDS3 mass dispatcher")
    p.add_argument("--target", type=int, required=True, choices=ALLOWED_TARGETS)
    p.add_argument(
        "--state-root",
        type=Path,
        default=Path(os.environ.get("DDS3_MASS_STATE_ROOT", DEFAULT_STATE_ROOT)),
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=Path(os.environ.get("BRIDGE_SCHOOL_REPO_ROOT", "/opt/bridge-school/bridge-video-free")),
    )
    args = p.parse_args()
    return run(args.target, args.state_root, args.repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
