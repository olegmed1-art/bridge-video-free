from __future__ import annotations

import json
import tempfile
from pathlib import Path

from continuation_tasks import continuation_tasks_from_line
from crossfit import annotate_file
from line_predictor import prediction_for
from shard_plan import build_shard_plan
from stage2_readiness import audit_stage2_readiness


DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def audit_fixture(work: Path) -> dict:
    return audit_stage2_readiness(work, expected_total_deals=4, expected_main_tasks=4)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        base = []
        for index in range(4):
            base.append({
                "task_id": f"P{index}-CT",
                "deal_id": f"P{index}",
                "board": 10_001 + index,
                "split": "train",
                "task_type": "contract_tricks",
                "deal": DEAL,
                "declarer": 2,
                "strain": 4,
                "leader": 3,
            })
        (work / "corpus_summary.json").write_text(json.dumps({"count": 4}), encoding="utf-8")
        write_jsonl(work / "blind_tasks.jsonl", base)
        annotate_file(work / "blind_tasks.jsonl", work / "blind_tasks_crossfit.jsonl", folds=2, seed=7)
        crossfit = [json.loads(x) for x in (work / "blind_tasks_crossfit.jsonl").read_text().splitlines() if x]
        plan = build_shard_plan(crossfit, stage="main", max_tasks=2, selected_splits={"train"})
        (work / "shard_plan_main.json").write_text(json.dumps(plan), encoding="utf-8")

        predictions = [
            prediction_for(task, cards_to_play=8, predictor_version="selftest")
            for task in crossfit
        ]
        write_jsonl(work / "stage2_line_preflight_tasks.jsonl", crossfit)
        write_jsonl(work / "stage2_line_preflight_predictions.jsonl", predictions)
        (work / "stage2_dds_play_preflight.json").write_text(
            json.dumps({"status": "ok", "invariant_violations": 0}), encoding="utf-8"
        )
        continuations = []
        for task, prediction in zip(crossfit, predictions):
            continuations.extend(
                continuation_tasks_from_line(
                    task,
                    prediction["line"],
                    prefix_indexes=[1, 4, 5, 8],
                    provenance="predicted_line",
                )
            )
        write_jsonl(work / "stage2_continuation_preflight.jsonl", continuations)
        (work / "family_model_selection_policy.json").write_text(
            json.dumps({"separate_families": True, "paired_bootstrap": True}), encoding="utf-8"
        )

        report = audit_fixture(work)
        assert report["main_train"]["ready"] is True, report
        assert report["holdout"]["ready"] is False
        assert report["skill_claim"]["ready"] is False
        assert report["main_train"]["actual_fresh_tasks"] == 4

        # OOF calibration closes the holdout gate without affecting skill claims.
        (work / "confidence_calibration_oof.json").write_text(
            json.dumps({"source": "out_of_fold_train_only"}), encoding="utf-8"
        )
        report2 = audit_fixture(work)
        assert report2["holdout"]["ready"] is True, report2

        # A pilot-only corpus must never be accepted as a complete 30k main scope.
        production_gate = audit_stage2_readiness(work)
        assert production_gate["main_train"]["ready"] is False
        assert any(x["code"] == "MAIN_CORPUS_NOT_EXPANDED" for x in production_gate["findings"])

        print(json.dumps({
            "ok": True,
            "main_train_ready_for_fixture": report["main_train"]["ready"],
            "production_30k_requirement_fail_closed": True,
            "holdout_fail_closed_before_oof": True,
            "holdout_ready_after_oof": report2["holdout"]["ready"],
            "skill_claim_still_blocked": not report2["skill_claim"]["ready"],
        }, indent=2))


if __name__ == "__main__":
    main()
