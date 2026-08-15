from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from audit import audit_database, audit_manifest, persist_audit
from checkpointing import sha256_file, snapshot_database
from config import ALGORITHM_VERSION, BATCH_SIZE_DD_TABLE, PROJECT_SEED, STAGES
from corpus import generate_corpus, validate_pbn_corpus
from dds_engine import contract_tricks_batch, engine_info, evaluate_opening_lead
from learning import (
    build_learning_plan,
    learning_allowed_for_task,
    persist_learning_plan,
    recompute_all_skills,
    record_skill_check,
    record_task_experience,
)
from storage import add_regression_case, connect, record_correction, upsert_prediction, upsert_result
from tasks import create_blind_tasks, load_locked_predictions
from variants import create_error_followups

CONFIRM_TOKEN = "YES"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def cmd_prepare(args) -> None:
    n = STAGES[args.stage]
    work = Path(args.out)
    work.mkdir(parents=True, exist_ok=True)
    summary = generate_corpus(n, work, PROJECT_SEED)
    validate_pbn_corpus(work / "raw.pbn", n)
    manifest_audit = audit_manifest(work / "manifest.jsonl")
    if manifest_audit["status"] != "ok":
        raise SystemExit(f"Manifest audit failed: {manifest_audit}")
    task_summary = create_blind_tasks(work / "raw.pbn", work / "manifest.jsonl", work / "blind_tasks.jsonl")
    state = {
        "stage": args.stage,
        "algorithm_version": ALGORITHM_VERSION,
        "status": "prepared_no_dds",
        "corpus": summary,
        "manifest_audit": manifest_audit,
        "tasks": task_summary,
        "next": "Create and lock blind predictions before any DDS evaluation.",
    }
    (work / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(json.dumps(state, indent=2))


def _require_start(args) -> None:
    if not args.start:
        raise SystemExit("DDS training blocked: --start was not supplied")
    if os.environ.get("DDS_TRAINING_CONFIRM") != CONFIRM_TOKEN:
        raise SystemExit("DDS training blocked: set DDS_TRAINING_CONFIRM=YES")
    splits = set(args.splits)
    if "sealed_test" in splits:
        if not args.open_sealed:
            raise SystemExit("Sealed test blocked: add --open-sealed only for final evaluation")
        if splits != {"sealed_test"}:
            raise SystemExit("Sealed test must run alone; do not mix it with train/validation/derived")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]


def _task_path(work: Path, value: str | None) -> Path:
    if not value:
        return work / "blind_tasks.jsonl"
    p = Path(value)
    return p if p.is_absolute() else work / p


def _corpus_hash(work: Path) -> str:
    p = work / "corpus_summary.json"
    if not p.exists():
        return "unknown"
    return str(json.loads(p.read_text(encoding="utf-8")).get("raw_sha256", "unknown"))


def _register_run(db, args, work: Path, task_path: Path, predictions_path: Path) -> None:
    splits_json = json.dumps(sorted(set(args.splits)), separators=(",", ":"))
    solver_json = json.dumps(engine_info(), ensure_ascii=False, sort_keys=True)
    expected = (
        args.stage,
        PROJECT_SEED,
        _corpus_hash(work),
        ALGORITHM_VERSION,
        splits_json,
        str(task_path),
        sha256_file(predictions_path),
        int(bool("sealed_test" in set(args.splits) and args.open_sealed)),
    )
    existing = db.execute(
        """
        SELECT stage,seed,corpus_sha256,algorithm_version,requested_splits_json,
               task_file,predictions_sha256,sealed_opened
        FROM runs WHERE run_id=?
        """,
        (args.run_id,),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != expected:
            raise ValueError(f"run_id {args.run_id} already exists with different provenance")
        db.execute("UPDATE runs SET status='running', completed_at=NULL WHERE run_id=?", (args.run_id,))
    else:
        db.execute(
            """
            INSERT INTO runs
              (run_id,stage,seed,corpus_sha256,solver_info_json,algorithm_version,
               requested_splits_json,task_file,predictions_sha256,sealed_opened,status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                args.run_id,
                args.stage,
                PROJECT_SEED,
                expected[2],
                solver_json,
                ALGORITHM_VERSION,
                splits_json,
                str(task_path),
                expected[6],
                expected[7],
                "running",
            ),
        )
    db.commit()


def _derived_transfer_evidence(db, task: dict, pred: dict, result: dict, run_id: str) -> list[str]:
    if not learning_allowed_for_task(task):
        return []
    source_id = task.get("derived_from_task_id")
    evidence_type = task.get("evidence_type")
    if not source_id or evidence_type not in {"symmetry", "perturbation", "transfer", "counterexample", "regression", "real_world"}:
        return []
    source_skills = [
        r[0] for r in db.execute(
            """
            SELECT DISTINCT skill_key FROM skill_evidence
            WHERE task_id=? AND outcome!='success' AND algorithm_version=?
            """,
            (source_id, ALGORITHM_VERSION),
        )
    ]
    success = result.get("error_code") == "OK"
    regret = result.get("dd_regret")
    if regret is None:
        regret = result.get("prediction_error")
    confidence = str(pred.get("confidence", "unknown")).lower()
    for skill_key in source_skills:
        record_skill_check(
            db,
            skill_key=skill_key,
            task_id=task["task_id"],
            deal_id=task["deal_id"],
            evidence_type=evidence_type,
            success=success,
            regret=None if regret is None else float(regret),
            confidence=confidence,
            run_id=run_id,
            split="derived",
            details={
                "derived_from_task_id": source_id,
                "source_split": task.get("source_split"),
                "source_root_split": task.get("source_root_split"),
                "error_code": result.get("error_code"),
                "prediction": pred,
                "result": result,
            },
        )
    return source_skills


def cmd_evaluate(args) -> None:
    _require_start(args)
    work = Path(args.work)
    task_path = _task_path(work, args.tasks_file)
    predictions_path = Path(args.predictions)
    tasks = load_jsonl(task_path)
    predictions = load_locked_predictions(predictions_path)
    requested = [t for t in tasks if t["split"] in set(args.splits)]
    if args.limit:
        requested = requested[: args.limit]
    missing = [t["task_id"] for t in requested if t["task_id"] not in predictions]
    if missing:
        raise SystemExit(f"DDS evaluation blocked: {len(missing)} requested tasks lack locked predictions; first={missing[0]}")

    db_path = work / "training.sqlite3"
    db = connect(db_path)
    _register_run(db, args, work, task_path, predictions_path)
    for task in requested:
        upsert_prediction(db, task, predictions[task["task_id"]])
    db.commit()

    existing_results = {
        task_id: json.loads(result_json)
        for task_id, result_json in db.execute("SELECT task_id,result_json FROM dds_results")
    }
    requested_ids = {t["task_id"] for t in requested}
    already_requested = sum(task_id in existing_results for task_id in requested_ids)
    existing_errors = sum(
        existing_results[task_id].get("error_code") != "OK"
        for task_id in requested_ids
        if task_id in existing_results
    )
    todo = [t for t in requested if t["task_id"] not in existing_results]
    contract_tasks = [t for t in todo if t["task_type"] == "contract_tricks"]

    table_values: dict[str, int] = {}
    for batch in _chunks(contract_tasks, BATCH_SIZE_DD_TABLE):
        matrices = contract_tricks_batch([t["deal"] for t in batch])
        for task, table in zip(batch, matrices):
            table_values[task["task_id"]] = int(table[task["strain"]][task["declarer"]])

    completed = 0
    error_count = 0
    learning_tasks_processed = 0
    derived_checks = 0
    last_snapshot = None
    for task in todo:
        pred = predictions[task["task_id"]]
        if task["task_type"] == "contract_tricks":
            actual = table_values[task["task_id"]]
            guessed = int(pred["tricks"])
            delta = guessed - actual
            # Analyzer revision is deliberately NOT stored in this immutable fact.
            # Version/provenance lives in `runs` and `skill_evidence`, so a future
            # analyzer can reinterpret the same DDS fact without creating a conflict.
            result = {
                "dds_tricks": actual,
                "predicted_tricks": guessed,
                "delta_pred_minus_dds": delta,
                "prediction_error": abs(delta),
                "dd_regret": None,
                "investigation_required": delta > 0,
                "error_code": "OK" if delta == 0 else "D_OVER_DDS_CLAIM" if delta > 0 else "D_MISSED_TRICKS",
            }
        else:
            result = evaluate_opening_lead(task["deal"], task["strain"], task["declarer"], pred["card"])
            expected = pred.get("expected_defense_tricks")
            over_claim = expected is not None and int(expected) > int(result["best_defense_tricks"])
            result["expected_defense_tricks"] = expected
            result["investigation_required"] = bool(over_claim)
            if not result["legal_or_equivalent"]:
                result["error_code"] = "F_ILLEGAL_OR_UNREPRESENTED_LEAD"
            elif result["dd_regret"] == 0:
                result["error_code"] = "OK"
            else:
                result["error_code"] = "F_OPENING_LEAD_REGRET"
            if over_claim:
                result["error_code"] = "F_DEFENSE_OVER_DDS_CLAIM"

        upsert_result(db, task, result)
        can_learn = learning_allowed_for_task(task)
        learned_skills: list[str] = []
        if can_learn:
            learning_tasks_processed += 1
            learned_skills = record_task_experience(db, task, pred, result, args.run_id)
            derived_checks += len(_derived_transfer_evidence(db, task, pred, result, args.run_id))

        if result.get("error_code") != "OK":
            error_count += 1
            db.execute(
                "INSERT INTO error_events(task_id,error_code,magnitude,details_json) VALUES(?,?,?,?)",
                (task["task_id"], result["error_code"], result.get("dd_regret", result.get("prediction_error")), json.dumps(result, ensure_ascii=False)),
            )
            if can_learn:
                add_regression_case(db, task, result, learned_skills[0] if learned_skills else None)

        completed += 1
        progress = already_requested + completed
        total_errors = existing_errors + error_count
        if completed % args.checkpoint_every == 0:
            next_id = todo[completed]["task_id"] if completed < len(todo) else None
            db.execute(
                "INSERT INTO checkpoints(run_id,completed_tasks,errors,next_task_id,note) VALUES(?,?,?,?,?)",
                (args.run_id, progress, total_errors, next_id, f"automatic checkpoint; algorithm={ALGORITHM_VERSION}"),
            )
            db.commit()
            if progress % args.snapshot_every == 0:
                last_snapshot = snapshot_database(
                    db,
                    db_path=db_path,
                    snapshot_dir=work / "checkpoints",
                    run_id=args.run_id,
                    completed_tasks=progress,
                    errors=total_errors,
                    next_task_id=next_id,
                    keep_milestone_every=args.milestone_every,
                )
            print(f"checkpoint: {progress}/{len(requested)}")

    recompute_all_skills(db)
    plan = build_learning_plan(db)
    learning_run = learning_tasks_processed > 0
    if learning_run:
        persist_learning_plan(db, plan, args.run_id)

    followups = None
    if learning_run and args.generate_followups and (work / "blind_tasks.jsonl").exists():
        followups = create_error_followups(
            work / "blind_tasks.jsonl",
            db,
            work / "derived_blind_tasks.jsonl",
            max_sources=args.max_followup_sources,
        )

    audit = audit_database(db)
    persist_audit(db, audit, args.run_id)
    db.execute(
        "UPDATE runs SET status=?, completed_at=CURRENT_TIMESTAMP WHERE run_id=?",
        ("completed" if audit["status"] == "ok" else "completed_with_audit_error", args.run_id),
    )
    db.commit()

    final_progress = already_requested + completed
    final_errors = existing_errors + error_count
    last_snapshot = snapshot_database(
        db,
        db_path=db_path,
        snapshot_dir=work / "checkpoints",
        run_id=args.run_id,
        completed_tasks=final_progress,
        errors=final_errors,
        next_task_id=None,
        keep_milestone_every=args.milestone_every,
    )
    print(json.dumps({
        "run_id": args.run_id,
        "algorithm_version": ALGORITHM_VERSION,
        "task_file": str(task_path),
        "evaluated_now": completed,
        "already_done_in_requested_set": already_requested,
        "requested": len(requested),
        "errors_now": error_count,
        "errors_in_requested_set": final_errors,
        "learning_tasks_processed": learning_tasks_processed,
        "holdout_tasks_processed": completed - learning_tasks_processed,
        "derived_skill_checks_recorded": derived_checks,
        "blind_followups": followups,
        "learning_plan_top": plan[:5],
        "checkpoint_snapshot": last_snapshot,
        "audit": audit,
        "solver": engine_info(),
    }, indent=2))


def cmd_reinterpret(args) -> None:
    """Rebuild current-version experience from immutable stored facts, no DDS call.

    This is used after an analyzer revision so historical predictions/results stay
    immutable while the new revision can derive a fresh skill interpretation.
    Holdouts remain excluded.
    """
    if not args.apply:
        raise SystemExit("Experience reinterpretation blocked: add --apply")
    work = Path(args.work)
    task_path = _task_path(work, args.tasks_file)
    tasks = load_jsonl(task_path)
    if args.limit:
        tasks = tasks[: args.limit]
    db = connect(work / "training.sqlite3")
    predictions = {k: json.loads(v) for k, v in db.execute("SELECT task_id,prediction_json FROM predictions")}
    results = {k: json.loads(v) for k, v in db.execute("SELECT task_id,result_json FROM dds_results")}

    processed = already_current = holdout_skipped = missing_facts = derived_checks = 0
    for task in tasks:
        if not learning_allowed_for_task(task):
            holdout_skipped += 1
            continue
        task_id = task["task_id"]
        if task_id not in predictions or task_id not in results:
            missing_facts += 1
            continue
        exists = db.execute(
            "SELECT 1 FROM skill_evidence WHERE task_id=? AND algorithm_version=? LIMIT 1",
            (task_id, ALGORITHM_VERSION),
        ).fetchone()
        if exists:
            already_current += 1
            continue
        pred, result = predictions[task_id], results[task_id]
        learned_skills = record_task_experience(db, task, pred, result, args.run_id)
        derived_checks += len(_derived_transfer_evidence(db, task, pred, result, args.run_id))
        if result.get("error_code") != "OK":
            add_regression_case(db, task, result, learned_skills[0] if learned_skills else None)
        processed += 1

    recompute_all_skills(db)
    plan = build_learning_plan(db)
    persist_learning_plan(db, plan, args.run_id)
    audit = audit_database(db)
    persist_audit(db, audit, args.run_id)
    db.commit()
    print(json.dumps({
        "algorithm_version": ALGORITHM_VERSION,
        "task_file": str(task_path),
        "processed": processed,
        "already_current": already_current,
        "holdout_skipped": holdout_skipped,
        "missing_facts": missing_facts,
        "derived_skill_checks_recorded": derived_checks,
        "dds_called": False,
        "learning_plan_top": plan[:5],
        "audit": audit,
    }, indent=2))


def cmd_followups(args) -> None:
    work = Path(args.work)
    db = connect(work / "training.sqlite3")
    summary = create_error_followups(
        work / "blind_tasks.jsonl",
        db,
        work / args.out,
        max_sources=args.max_sources,
    )
    print(json.dumps(summary, indent=2))


def cmd_plan(args) -> None:
    db = connect(Path(args.work) / "training.sqlite3")
    recompute_all_skills(db)
    plan = build_learning_plan(db, args.limit)
    if args.persist:
        persist_learning_plan(db, plan, args.run_id)
        db.commit()
    print(json.dumps({"algorithm_version": ALGORITHM_VERSION, "plan": plan}, indent=2))


def cmd_audit(args) -> None:
    work = Path(args.work)
    db = connect(work / "training.sqlite3")
    report = audit_database(db)
    if (work / "manifest.jsonl").exists():
        report["manifest"] = audit_manifest(work / "manifest.jsonl")
    persist_audit(db, report, args.run_id)
    db.commit()
    print(json.dumps(report, indent=2))
    if args.fail_on_error and report["status"] != "ok":
        raise SystemExit(2)


def cmd_correct(args) -> None:
    db = connect(Path(args.work) / "training.sqlite3")
    replacement = None
    if args.replacement_json:
        replacement = json.loads(Path(args.replacement_json).read_text(encoding="utf-8"))
    correction_id = record_correction(
        db,
        target_table=args.target_table,
        target_key=args.target_key,
        correction_type=args.correction_type,
        reason=args.reason,
        replacement=replacement,
        supersedes_correction_id=args.supersedes,
    )
    db.commit()
    print(json.dumps({"correction_id": correction_id, "target": f"{args.target_table}:{args.target_key}"}, indent=2))


def cmd_report(args) -> None:
    from report import generate_report
    path = generate_report(Path(args.work), args.stage)
    print(path)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fail-closed DDS learning runner")
    sp = p.add_subparsers(dest="command", required=True)

    q = sp.add_parser("prepare", help="Generate RAW corpus and blind tasks; does not call DDS")
    q.add_argument("--stage", choices=STAGES, required=True)
    q.add_argument("--out", required=True)
    q.set_defaults(func=cmd_prepare)

    q = sp.add_parser("evaluate", help="Evaluate only locked blind predictions using local DDS3")
    q.add_argument("--stage", choices=STAGES, required=True)
    q.add_argument("--work", required=True)
    q.add_argument("--tasks-file", help="Task JSONL relative to --work; default blind_tasks.jsonl")
    q.add_argument("--predictions", required=True)
    q.add_argument("--splits", nargs="+", choices=("train", "validation", "sealed_test", "derived"), default=["train"])
    q.add_argument("--start", action="store_true")
    q.add_argument("--open-sealed", action="store_true")
    q.add_argument("--limit", type=int)
    q.add_argument("--checkpoint-every", type=int, default=100)
    q.add_argument("--snapshot-every", type=int, default=1000)
    q.add_argument("--milestone-every", type=int, default=5000)
    q.add_argument("--run-id", default=None)
    q.add_argument("--generate-followups", action=argparse.BooleanOptionalAction, default=True)
    q.add_argument("--max-followup-sources", type=int, default=500)
    q.set_defaults(func=cmd_evaluate)

    q = sp.add_parser("reinterpret", help="Rebuild current-version learning from stored facts; never calls DDS")
    q.add_argument("--work", required=True)
    q.add_argument("--tasks-file", help="Task JSONL relative to --work; default blind_tasks.jsonl")
    q.add_argument("--limit", type=int)
    q.add_argument("--apply", action="store_true")
    q.add_argument("--run-id", default=None)
    q.set_defaults(func=cmd_reinterpret)

    q = sp.add_parser("followups", help="Create blind symmetry/perturbation tasks from TRAIN errors only; no DDS call")
    q.add_argument("--work", required=True)
    q.add_argument("--out", default="derived_blind_tasks.jsonl")
    q.add_argument("--max-sources", type=int, default=500)
    q.set_defaults(func=cmd_followups)

    q = sp.add_parser("plan", help="Rank weaknesses and propose targeted transfer/counterexample/regression work")
    q.add_argument("--work", required=True)
    q.add_argument("--limit", type=int, default=20)
    q.add_argument("--persist", action="store_true")
    q.add_argument("--run-id", default=None)
    q.set_defaults(func=cmd_plan)

    q = sp.add_parser("audit", help="Audit database provenance and immutability without running DDS")
    q.add_argument("--work", required=True)
    q.add_argument("--fail-on-error", action="store_true")
    q.add_argument("--run-id", default=None)
    q.set_defaults(func=cmd_audit)

    q = sp.add_parser("correct", help="Append a correction; never rewrite a locked fact")
    q.add_argument("--work", required=True)
    q.add_argument("--target-table", required=True)
    q.add_argument("--target-key", required=True)
    q.add_argument("--correction-type", required=True)
    q.add_argument("--reason", required=True)
    q.add_argument("--replacement-json")
    q.add_argument("--supersedes", type=int)
    q.set_defaults(func=cmd_correct)

    q = sp.add_parser("report")
    q.add_argument("--stage", choices=STAGES, required=True)
    q.add_argument("--work", required=True)
    q.set_defaults(func=cmd_report)
    return p


def main() -> None:
    p = parser()
    args = p.parse_args()
    if getattr(args, "run_id", None) is None:
        args.run_id = uuid.uuid4().hex[:12]
    args.func(args)


if __name__ == "__main__":
    main()
