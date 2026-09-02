from __future__ import annotations

"""Prepare the refined DDS Learning Stage 2B candidate v2.5.

This command is a read-only successor to ``stage2b_prepare.py``.  It keeps the
first real v2.4 evidence as an immutable checkpoint, fixes the weaknesses found
there, and produces a new compact preparation artifact without calling DDS or
opening validation/sealed data.
"""

import argparse
import json
import sqlite3
import tarfile
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from continuation_tasks import continuation_tasks_from_line
from line_predictor import generate_line
from stage2b_blueprint import write_multi_contract_blueprint
from stage2b_prepare import (
    classify_investigations,
    extract_counterexample_candidates,
    load_tasks,
    load_train_facts,
    prepare_oof_candidate,
    select_line_source_tasks,
    write_jsonl,
)
from stage2b_v24 import (
    aggregate_review_queue,
    build_current_stage_manifest,
    sha256_file,
    write_json,
)
from stage2b_v25 import (
    CANDIDATE_ALGORITHM_VERSION,
    calibration_diagnostics,
    enrich_review_rows,
    exact_balanced_curriculum,
    recalibrate_oof_rows,
    stratified_oof_comparison,
)


def _line_wave_and_curriculum(
    oof_rows: Sequence[Mapping[str, object]],
    *,
    source_total: int,
    per_actor: int,
    line_cards: int,
) -> tuple[list[dict], list[dict], dict]:
    sources = select_line_source_tasks(oof_rows, source_total=source_total)
    line_rows = []
    candidates = []
    for source in sources:
        task = source["task"]
        line = generate_line(task, cards_to_play=line_cards)
        line_rows.append(
            {
                "task_id": source["task_id"],
                "task": task,
                "line": line,
                "line_policy": "greedy-cheapest-winner-v1",
                "line_cards": len(line),
                "locked": True,
                "blind": True,
                "priority": source["priority"],
                "severity": source["severity"],
                "control": source["control"],
            }
        )
        for continuation in continuation_tasks_from_line(task, line, provenance="predicted_line"):
            continuation["priority"] = float(source["priority"])
            continuation["severity"] = float(source["severity"])
            continuation["source_control"] = bool(source["control"])
            candidates.append(continuation)

    curriculum = exact_balanced_curriculum(candidates, per_actor=per_actor, seed=20260818)
    source_summary = {
        "requested_sources": source_total,
        "actual_sources": len(line_rows),
        "candidate_continuations": len(candidates),
        "candidate_by_actor": dict(Counter(row["actor"] for row in candidates)),
        "selected_by_actor": dict(Counter(row["actor"] for row in curriculum)),
        "exact_balance_required": True,
    }
    return line_rows, curriculum, source_summary


def _review_source_rows(
    con: sqlite3.Connection,
    task_metadata: Mapping[str, Mapping[str, object]],
) -> list[dict]:
    rows = []
    for skill_key, task_id, deal_id, regret, confidence, evidence_json in con.execute(
        """
        SELECT skill_key,task_id,deal_id,COALESCE(regret,0),confidence,evidence_json
        FROM skill_evidence WHERE outcome!='success' ORDER BY id
        """
    ):
        payload = json.loads(evidence_json)
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        prediction = payload.get("prediction", {}) if isinstance(payload, dict) else {}
        rows.append(
            {
                "skill_key": str(skill_key),
                "task_id": str(task_id),
                "deal_id": str(deal_id),
                "error_code": str(result.get("error_code", "unknown")),
                "mechanism": str(result.get("mechanism", result.get("error_code", "unknown"))),
                "due_window": "stage2b-v25",
                "severity": float(regret or 0.0),
                "confidence_probability": float(
                    prediction.get(
                        "confidence_probability",
                        0.75 if confidence == "high" else 0.50 if confidence == "medium" else 0.25,
                    )
                ),
                "requested_tasks": 1,
            }
        )
    return enrich_review_rows(rows, task_metadata)


def prepare_stage2b_v25(
    *,
    work: Path,
    task_paths: Sequence[Path],
    out_dir: Path,
    main_tasks_path: Path,
    line_source_total: int = 650,
    continuations_per_actor: int = 1000,
    line_cards: int = 16,
    blueprint_families: int = 500,
) -> dict:
    db_path = work / "training.sqlite3"
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    tasks = load_tasks(task_paths)
    rows = load_train_facts(db_path, tasks)

    base_oof = prepare_oof_candidate(rows)
    oof_rows, calibrator = recalibrate_oof_rows(
        base_oof["oof_rows"],
        minimum_support=50,
        review_threshold=0.65,
    )
    comparison = stratified_oof_comparison(oof_rows)
    diagnostics = calibration_diagnostics(oof_rows)
    family_policy = comparison["family_policy"]

    full_candidate_model = dict(base_oof["full_candidate_model"])
    full_candidate_model["algorithm_version"] = CANDIDATE_ALGORITHM_VERSION
    full_candidate_model["raw_probability_policy"] = "support-aware-v2"
    full_candidate_model["family_selection_policy"] = family_policy

    line_rows, curriculum, line_summary = _line_wave_and_curriculum(
        oof_rows,
        source_total=line_source_total,
        per_actor=continuations_per_actor,
        line_cards=line_cards,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    oof_path = out_dir / "oof_predictions_v03_refined.jsonl"
    write_jsonl(
        oof_path,
        [
            {
                "task_id": row["task_id"],
                "task_type": row["task_type"],
                "strain": row["strain"],
                "family_id": row["family_id"],
                "heldout_fold": row["heldout_fold"],
                "prediction": row["prediction"],
                "source_prediction": row["source_prediction"],
                "result": row["result"],
            }
            for row in oof_rows
        ],
    )
    model_path = out_dir / "candidate_model_v03_refined.json"
    calibration_path = out_dir / "oof_calibration_v25.json"
    diagnostics_path = out_dir / "calibration_diagnostics_v25.json"
    comparison_path = out_dir / "oof_comparison_v03_stratified.json"
    family_policy_path = out_dir / "family_selection_policy.json"
    lines_path = out_dir / "line_wave_sources_v25.jsonl"
    curriculum_path = out_dir / "continuation_curriculum_balanced.jsonl"
    write_json(model_path, full_candidate_model)
    write_json(calibration_path, calibrator)
    write_json(diagnostics_path, diagnostics)
    write_json(comparison_path, comparison)
    write_json(family_policy_path, family_policy)
    write_jsonl(lines_path, line_rows)
    write_jsonl(curriculum_path, curriculum)

    counterexamples = extract_counterexample_candidates(db_path, task_paths)
    counterexample_path = out_dir / "counterexample_candidates.jsonl"
    write_jsonl(counterexample_path, counterexamples)

    blueprint_path = out_dir / "multi_contract_blueprint.jsonl"
    blueprint = write_multi_contract_blueprint(
        main_tasks_path,
        blueprint_path,
        family_limit=blueprint_families,
    )
    expected_blueprint_tasks = blueprint_families * 20
    if int(blueprint["tasks"]) != expected_blueprint_tasks:
        raise ValueError(
            f"Multi-contract blueprint incomplete: {blueprint['tasks']} != {expected_blueprint_tasks}"
        )

    con = sqlite3.connect(db_path)
    investigations = classify_investigations(con)
    review_rows = _review_source_rows(con, tasks)
    queue = aggregate_review_queue(
        review_rows,
        max_tasks_per_group=250,
        representative_limit=10,
    )
    dds_results_count = con.execute("SELECT COUNT(*) FROM dds_results").fetchone()[0]
    con.close()

    investigations_path = out_dir / "investigation_resolution_classes.json"
    queue_path = out_dir / "review_queue_projection_enriched.json"
    write_json(investigations_path, investigations)
    write_json(queue_path, queue)

    selected_families = {
        family: details["selected_for_future_validation"]
        for family, details in family_policy["families"].items()
    }
    readiness = {
        "schema": "dds-stage2b-readiness-v2",
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "train_rows": len(rows),
        "oof_rows": len(oof_rows),
        "folds": base_oof["folds"],
        "line_wave": line_summary,
        "continuation_tasks": len(curriculum),
        "continuation_by_actor": dict(Counter(row["actor"] for row in curriculum)),
        "counterexample_candidates": len(counterexamples),
        "multi_contract_blueprint": blueprint,
        "family_selection": selected_families,
        "investigations": {
            "total": len(investigations),
            "structural": sum(row["resolution_status"] == "resolved_structurally" for row in investigations),
            "card_level": sum(row["resolution_status"] == "resolved_at_card_level" for row in investigations),
        },
        "review_queue_groups": len(queue),
        "review_queue_strains": sorted({str(row["strain"]) for row in queue}),
        "validation_opened": False,
        "sealed_opened": False,
        "dds_called": False,
        "mass_training_started": False,
        "next_gate": "blind_continuation_counterexample_and_multicontract_predictions",
    }
    readiness_path = out_dir / "stage2b_readiness_v25.json"
    write_json(readiness_path, readiness)

    manifest_path = out_dir / "CURRENT_STAGE_MANIFEST.json"
    manifest = build_current_stage_manifest(
        current_stage="stage2b_v25_prepared",
        current_algorithm=CANDIDATE_ALGORITHM_VERSION,
        canonical_files={
            "database": db_path,
            "candidate_model": model_path,
            "oof_calibration": calibration_path,
            "calibration_diagnostics": diagnostics_path,
            "oof_comparison": comparison_path,
            "family_selection_policy": family_policy_path,
            "line_wave": lines_path,
            "continuation_curriculum": curriculum_path,
            "counterexample_candidates": counterexample_path,
            "multi_contract_blueprint": blueprint_path,
            "investigation_classes": investigations_path,
            "review_queue_projection": queue_path,
            "readiness": readiness_path,
        },
        holdout_status="closed",
        sealed_status="closed",
        next_gate="blind_continuation_counterexample_and_multicontract_predictions",
        metadata={
            "dds_results": dds_results_count,
            "oof_rows": len(oof_rows),
            "continuation_tasks": len(curriculum),
            "counterexample_candidates": len(counterexamples),
            "multi_contract_blueprint_tasks": int(blueprint["tasks"]),
        },
    )
    write_json(manifest_path, manifest)

    archive_path = out_dir / "dds-stage2b-v25-prepared-compact.tgz"
    canonical_paths = [
        db_path,
        model_path,
        calibration_path,
        diagnostics_path,
        comparison_path,
        family_policy_path,
        oof_path,
        lines_path,
        curriculum_path,
        counterexample_path,
        blueprint_path,
        investigations_path,
        queue_path,
        readiness_path,
        manifest_path,
    ]
    with tarfile.open(archive_path, "w:gz") as archive:
        for path in canonical_paths:
            archive.add(path, arcname=f"stage2b-v25/{path.name}", recursive=False)
    archive_sha = sha256_file(archive_path)
    archive_sha_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    archive_sha_path.write_text(f"{archive_sha}  {archive_path.name}\n", encoding="utf-8")

    summary = {
        "schema": "dds-stage2b-preparation-summary-v2",
        "algorithm_version": CANDIDATE_ALGORITHM_VERSION,
        "readiness": readiness,
        "comparison": comparison,
        "calibration_diagnostics": diagnostics,
        "artifacts": {
            "manifest": str(manifest_path),
            "archive": str(archive_path),
            "archive_sha256": archive_sha,
        },
    }
    write_json(out_dir / "stage2b_summary_v25.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare refined Stage 2B v2.5 from immutable TRAIN facts without DDS/holdout exposure"
    )
    parser.add_argument("--work", required=True)
    parser.add_argument("--tasks", nargs="+", required=True)
    parser.add_argument("--main-tasks", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--line-sources", type=int, default=650)
    parser.add_argument("--continuations-per-actor", type=int, default=1000)
    parser.add_argument("--line-cards", type=int, default=16)
    parser.add_argument("--blueprint-families", type=int, default=500)
    return parser


def main() -> None:
    args = parser().parse_args()
    summary = prepare_stage2b_v25(
        work=Path(args.work),
        task_paths=[Path(path) for path in args.tasks],
        out_dir=Path(args.out),
        main_tasks_path=Path(args.main_tasks),
        line_source_total=args.line_sources,
        continuations_per_actor=args.continuations_per_actor,
        line_cards=args.line_cards,
        blueprint_families=args.blueprint_families,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
