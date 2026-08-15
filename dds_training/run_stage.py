from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from audit import audit_database, audit_manifest, persist_audit
from config import ALGORITHM_VERSION, BATCH_SIZE_DD_TABLE, PROJECT_SEED, STAGES
from corpus import generate_corpus, validate_pbn_corpus
from dds_engine import contract_tricks_batch, engine_info, evaluate_opening_lead
from learning import build_learning_plan, persist_learning_plan, recompute_all_skills, record_task_experience
from storage import add_regression_case, connect, record_correction, upsert_prediction, upsert_result
from tasks import create_blind_tasks, load_locked_predictions

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
    if "sealed_test" in args.splits and not args.open_sealed:
        raise SystemExit("Sealed test blocked: add --open-sealed only for the final stage evaluation")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]


def cmd_evaluate(args) -> None:
    _require_start(args)
    work = Path(args.work)
    tasks = load_jsonl(work / "blind_tasks.jsonl")
    predictions = load_locked_predictions(Path(args.predictions))
    requested = [t for t in tasks if t["split"] in set(args.splits)]
    if args.limit:
        requested = requested[: args.limit]
    missing = [t["task_id"] for t in requested if t["task_id"] not in predictions]
    if missing:
        raise SystemExit(f"DDS evaluation blocked: {len(missing)} requested tasks lack locked predictions; first={missing[0]}")

    db = connect(work / "training.sqlite3")
    for task in requested:
        upsert_prediction(db, task, predictions[task["task_id"]])
    db.commit()

    already = {r[0] for r in db.execute("SELECT task_id FROM dds_results")}
    todo = [t for t in requested if t["task_id"] not in already]
    contract_tasks = [t for t in todo if t["task_type"] == "contract_tricks"]

    # Batch DD tables for contract-trick tasks.
    table_values: dict[str, int] = {}
    for batch in _chunks(contract_tasks, BATCH_SIZE_DD_TABLE):
        matrices = contract_tricks_batch([t["deal"] for t in batch])
        for task, table in zip(batch, matrices):
            table_values[task["task_id"]] = int(table[task["strain"]][task["declarer"]])

    completed = 0
    error_count = 0
    for task in todo:
        pred = predictions[task["task_id"]]
        if task["task_type"] == "contract_tricks":
            actual = table_values[task["task_id"]]
            guessed = int(pred["tricks"])
            delta = guessed - actual
            result = {
                "dds_tricks": actual,
                "predicted_tricks": guessed,
                "delta_pred_minus_dds": delta,
                "prediction_error": abs(delta),
                "dd_regret": None,
                "investigation_required": delta > 0,
                "error_code": "OK" if delta == 0 else "D_OVER_DDS_CLAIM" if delta > 0 else "D_MISSED_TRICKS",
                "algorithm_version": ALGORITHM_VERSION,
            }
        else:
            result = evaluate_opening_lead(task["deal"], task["strain"], task["declarer"], pred["card"])
            expected = pred.get("expected_defense_tricks")
            over_claim = expected is not None and int(expected) > int(result["best_defense_tricks"])
            result["expected_defense_tricks"] = expected
            result["investigation_required"] = bool(over_claim)
            result["algorithm_version"] = ALGORITHM_VERSION
            if not result["legal_or_equivalent"]:
                result["error_code"] = "F_ILLEGAL_OR_UNREPRESENTED_LEAD"
            elif result["dd_regret"] == 0:
                result["error_code"] = "OK"
            else:
                result["error_code"] = "F_OPENING_LEAD_REGRET"
            if over_claim:
                result["error_code"] = "F_DEFENSE_OVER_DDS_CLAIM"

        upsert_result(db, task, result)
        learned_skills = record_task_experience(db, task, pred, result, args.run_id)
        if result.get("error_code") != "OK":
            error_count += 1
            db.execute(
                "INSERT INTO error_events(task_id,error_code,magnitude,details_json) VALUES(?,?,?,?)",
                (task["task_id"], result["error_code"], result.get("dd_regret", result.get("prediction_error")), json.dumps(result, ensure_ascii=False)),
            )
            # Every meaningful failure becomes a permanent regression case.  This
            # records the old failure without changing the DDS fact itself.
            add_regression_case(db, task, result, learned_skills[0] if learned_skills else None)
        completed += 1
        if completed % args.checkpoint_every == 0:
            next_id = todo[completed]["task_id"] if completed < len(todo) else None
            db.execute(
                "INSERT INTO checkpoints(run_id,completed_tasks,errors,next_task_id,note) VALUES(?,?,?,?,?)",
                (args.run_id, completed, error_count, next_id, f"automatic checkpoint; algorithm={ALGORITHM_VERSION}"),
            )
            db.commit()
            print(f"checkpoint: {completed}/{len(todo)}")

    recompute_all_skills(db)
    plan = build_learning_plan(db)
    persist_learning_plan(db, plan, args.run_id)
    audit = audit_database(db)
    persist_audit(db, audit, args.run_id)
    db.commit()
    print(json.dumps({
        "run_id": args.run_id,
        "algorithm_version": ALGORITHM_VERSION,
        "evaluated_now": completed,
        "already_done": len(requested) - len(todo),
        "requested": len(requested),
        "errors_now": error_count,
        "learning_plan_top": plan[:5],
        "audit": audit,
        "solver": engine_info(),
    }, indent=2))


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
    q.add_argument("--predictions", required=True)
    q.add_argument("--splits", nargs="+", choices=("train", "validation", "sealed_test"), default=["train"])
    q.add_argument("--start", action="store_true")
    q.add_argument("--open-sealed", action="store_true")
    q.add_argument("--limit", type=int)
    q.add_argument("--checkpoint-every", type=int, default=100)
    q.add_argument("--run-id", default=None)
    q.set_defaults(func=cmd_evaluate)

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
