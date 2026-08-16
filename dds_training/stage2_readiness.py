from __future__ import annotations

"""Phased readiness gates for the 30k DDS stage.

Starting TRAIN, opening holdouts and claiming stable skills require different
evidence.  A single binary gate either blocked too much or allowed unsupported
claims.  This module separates those decisions and remains fail-closed.
"""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

from crossfit import audit_tasks
from playline import PlayLineError, validate_prediction_line


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _capabilities() -> dict:
    checks = {}
    imports = {
        "legal_play_reconstruction": ("playline", "replay_line"),
        "line_bearing_predictor": ("line_predictor", "prediction_for"),
        "dds_full_play_trajectory": ("dds_play", "analyse_line"),
        "continuation_task_generator": ("continuation_tasks", "continuation_tasks_from_line"),
        "family_crossfit": ("crossfit", "annotate_file"),
        "restartable_shards": ("shard_plan", "build_shard_plan"),
        "oof_confidence_calibration": ("confidence_calibration", "fit_calibrator"),
        "counterexample_candidate_extraction": ("counterexample_candidates", "extract_candidates"),
    }
    for name, (module_name, attribute) in imports.items():
        try:
            module = __import__(module_name, fromlist=[attribute])
            checks[name] = callable(getattr(module, attribute))
        except Exception as exc:  # fail closed, but preserve the reason
            checks[name] = False
            checks[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
    checks["paid_dds_api_required"] = False
    return checks


def audit_stage2_readiness(work: Path) -> dict:
    findings: list[dict] = []

    def add(gate: str, code: str, detail: str, severity: str = "error") -> None:
        findings.append({"gate": gate, "code": code, "severity": severity, "detail": detail})

    capabilities = _capabilities()
    for name, value in capabilities.items():
        if name.endswith("_error") or name == "paid_dds_api_required":
            continue
        if value is not True:
            add("main_train", f"CAPABILITY_{name.upper()}", f"Required capability {name} is unavailable")

    base_tasks = _load_jsonl(work / "blind_tasks.jsonl")
    crossfit_tasks = _load_jsonl(work / "blind_tasks_crossfit.jsonl")
    if not base_tasks:
        add("main_train", "BASE_TASKS_MISSING", "blind_tasks.jsonl is missing or empty")
    if not crossfit_tasks:
        add("main_train", "CROSSFIT_TASKS_MISSING", "Generate blind_tasks_crossfit.jsonl before Stage 2")
        crossfit_audit = {"status": "error", "reason": "missing"}
    else:
        crossfit_audit = audit_tasks(crossfit_tasks)
        if crossfit_audit["status"] != "ok":
            add("main_train", "CROSSFIT_FAMILY_LEAK", f"Cross-fit audit failed: {crossfit_audit}")
        if base_tasks and len(crossfit_tasks) != len(base_tasks):
            add("main_train", "CROSSFIT_COVERAGE", "Cross-fit task count does not match base task count")

    shard_path = work / "shard_plan_main.json"
    shard_plan = json.loads(shard_path.read_text(encoding="utf-8")) if shard_path.exists() else {}
    if shard_plan.get("schema") != "dds-shard-plan-v1":
        add("main_train", "SHARD_PLAN_MISSING", "Create a deterministic family-safe shard_plan_main.json")
    elif crossfit_tasks and int(shard_plan.get("task_count", -1)) != len(crossfit_tasks):
        add("main_train", "SHARD_PLAN_COVERAGE", "Shard plan does not cover every cross-fit task")
    elif shard_plan.get("family_safe") is not True or shard_plan.get("restartable") is not True:
        add("main_train", "SHARD_PLAN_UNSAFE", "Shard plan must be family-safe and restartable")

    preflight_tasks = {
        str(task["task_id"]): task for task in _load_jsonl(work / "stage2_line_preflight_tasks.jsonl")
    }
    preflight_predictions = _load_jsonl(work / "stage2_line_preflight_predictions.jsonl")
    legal_lines = 0
    if not preflight_tasks or not preflight_predictions:
        add("main_train", "LINE_PREFLIGHT_MISSING", "Run a blind legal-line preflight before Stage 2")
    else:
        for prediction in preflight_predictions:
            task = preflight_tasks.get(str(prediction.get("task_id")))
            if task is None:
                add("main_train", "LINE_PREFLIGHT_TASK_MISMATCH", f"No task for prediction {prediction.get('task_id')}")
                continue
            try:
                validate_prediction_line(task, prediction, require_nonempty=True)
                legal_lines += 1
            except PlayLineError as exc:
                add("main_train", "ILLEGAL_PREFLIGHT_LINE", f"{prediction.get('task_id')}: {exc}")
        if legal_lines < min(4, len(preflight_predictions)):
            add("main_train", "INSUFFICIENT_LEGAL_LINES", f"Only {legal_lines} legal preflight lines")

    trajectory_path = work / "stage2_dds_play_preflight.json"
    trajectory = json.loads(trajectory_path.read_text(encoding="utf-8")) if trajectory_path.exists() else {}
    if trajectory.get("status") != "ok" or trajectory.get("invariant_violations", 1) != 0:
        add("main_train", "DDS_PLAY_PREFLIGHT_MISSING", "DDS full-play normalization preflight is absent or invalid")

    continuation = _load_jsonl(work / "stage2_continuation_preflight.jsonl")
    actors = Counter(str(x.get("actor")) for x in continuation)
    if not continuation or actors["declarer"] == 0 or actors["defense"] == 0:
        add("main_train", "CONTINUATION_PREFLIGHT_IMBALANCE", "Preflight must contain both declarer and defense continuation tasks")

    # Holdout gate: may be completed after Stage 2 TRAIN, but before validation is opened.
    calibration_path = work / "confidence_calibration_oof.json"
    calibration = json.loads(calibration_path.read_text(encoding="utf-8")) if calibration_path.exists() else {}
    if calibration.get("source") != "out_of_fold_train_only":
        add("holdout", "OOF_CALIBRATION_MISSING", "Fit confidence only from out-of-fold TRAIN residuals before validation")
    family_selection_path = work / "family_model_selection_policy.json"
    family_selection = json.loads(family_selection_path.read_text(encoding="utf-8")) if family_selection_path.exists() else {}
    if family_selection.get("separate_families") is not True or family_selection.get("paired_bootstrap") is not True:
        add("holdout", "FAMILY_SELECTION_POLICY_MISSING", "Contract and defense families require separate paired-bootstrap selection")

    # Claim gate: Stage 2 may start without these, but no skill may be called stable.
    db_path = work / "training.sqlite3"
    verified_counterexamples = rules = real_world = 0
    if db_path.exists():
        con = sqlite3.connect(db_path)
        try:
            verified_counterexamples = con.execute("SELECT COUNT(*) FROM counterexamples").fetchone()[0]
            rules = con.execute("SELECT COUNT(*) FROM rule_versions WHERE status IN ('confirmed','stable')").fetchone()[0]
            real_world = con.execute(
                "SELECT COUNT(*) FROM skill_evidence WHERE evidence_type='real_world' AND outcome='success'"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            pass
    if verified_counterexamples == 0:
        add("skill_claim", "NO_VERIFIED_COUNTEREXAMPLES", "Stable skill claims require passed blind counterexamples", "warning")
    if rules == 0:
        add("skill_claim", "NO_CONFIRMED_RULES", "No tested versioned bridge rule is confirmed", "warning")
    if real_world == 0:
        add("skill_claim", "NO_REAL_WORLD_TRANSFER", "No successful real-play transfer evidence exists", "warning")

    def gate_ready(gate: str) -> bool:
        return not any(x["gate"] == gate and x["severity"] == "error" for x in findings)

    return {
        "schema": "dds-stage2-readiness-v1",
        "capabilities": capabilities,
        "main_train": {
            "ready": gate_ready("main_train"),
            "legal_preflight_lines": legal_lines,
            "crossfit_audit": crossfit_audit,
            "shard_count": shard_plan.get("shard_count", 0),
            "continuation_by_actor": dict(actors),
        },
        "holdout": {
            "ready": gate_ready("holdout"),
            "calibration_source": calibration.get("source"),
            "family_selection_policy": family_selection,
        },
        "skill_claim": {
            "ready": gate_ready("skill_claim") and verified_counterexamples > 0 and rules > 0 and real_world > 0,
            "verified_counterexamples": verified_counterexamples,
            "confirmed_rules": rules,
            "successful_real_world_evidence": real_world,
        },
        "findings": findings,
        "paid_api_required": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit phased readiness for DDS Stage 2")
    parser.add_argument("--work", required=True)
    parser.add_argument("--out")
    parser.add_argument("--require", choices=("main_train", "holdout", "skill_claim"))
    args = parser.parse_args()
    report = audit_stage2_readiness(Path(args.work))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.require and not report[args.require]["ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
