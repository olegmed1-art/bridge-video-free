from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from compare_predictors import _select_family
from corpus import generate_corpus
from experience_events import schedule_spaced_reviews
from learning import record_skill_check
from storage import connect, upsert_result
from tasks import create_blind_tasks
from variants import create_error_followups, rotate_task


def _write_manifest_tasks(root: Path) -> list[dict]:
    generate_corpus(15, root, seed=20260815)
    create_blind_tasks(root / "raw.pbn", root / "manifest.jsonl", root / "all_tasks.jsonl")
    all_tasks = [json.loads(x) for x in (root / "all_tasks.jsonl").read_text().splitlines() if x.strip()]
    deals = {}
    for task in all_tasks:
        deals.setdefault(task["deal_id"], task)
    selected_deals = list(deals.values())[:15]
    tasks = []
    groups = [
        ("contract_tricks", "D_MISSED_TRICKS"),
        ("contract_tricks", "D_OVER_DDS_CLAIM"),
        ("opening_lead", "F_OPENING_LEAD_REGRET"),
    ]
    for i, source in enumerate(selected_deals):
        task_type, error_code = groups[i % len(groups)]
        task = dict(source)
        task["task_id"] = f"SELF-{i:02d}-{'CT' if task_type == 'contract_tricks' else 'OL'}"
        task["task_type"] = task_type
        task["strain"] = i % 5
        task["strain_name"] = ("S", "H", "D", "C", "NT")[i % 5]
        task["split"] = "train"
        task["expected_error_code"] = error_code
        if task_type == "opening_lead":
            task["leader"] = (int(task["declarer"]) + 1) % 4
        else:
            task.pop("leader", None)
        tasks.append(task)
    with (root / "blind_tasks.jsonl").open("w", encoding="utf-8") as f:
        for task in tasks:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
    return tasks


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        tasks = _write_manifest_tasks(root)
        con = connect(root / "training.sqlite3")

        for i, task in enumerate(tasks):
            code = task["expected_error_code"]
            if task["task_type"] == "contract_tricks":
                result = {
                    "dds_tricks": 8,
                    "predicted_tricks": 7 if code == "D_MISSED_TRICKS" else 9,
                    "delta_pred_minus_dds": -1 if code == "D_MISSED_TRICKS" else 1,
                    "prediction_error": 1,
                    "dd_regret": None,
                    "investigation_required": code == "D_OVER_DDS_CLAIM",
                    "error_code": code,
                }
            else:
                result = {
                    "scores": {"SA": 4, "S2": 3},
                    "best_defense_tricks": 4,
                    "optimal_cards": ["SA"],
                    "chosen_card": "S2",
                    "chosen_defense_tricks": 3,
                    "legal_or_equivalent": True,
                    "dd_regret": 1 + (i % 2),
                    "investigation_required": False,
                    "error_code": code,
                }
            upsert_result(con, task, result)
            con.execute(
                "INSERT INTO error_events(task_id,error_code,magnitude,details_json) VALUES(?,?,?,?)",
                (task["task_id"], code, result.get("dd_regret") or result.get("prediction_error"), json.dumps(result)),
            )
        con.commit()

        followups = create_error_followups(
            root / "blind_tasks.jsonl",
            con,
            root / "derived_blind_tasks.jsonl",
            max_sources=15,
            batch_id="methodology-v22-selftest",
        )
        derived = [json.loads(x) for x in (root / "derived_blind_tasks.jsonl").read_text().splitlines() if x.strip()]
        assert followups["source_types"] == {"contract_tricks": 10, "opening_lead": 5}, followups
        assert set(followups["source_error_codes"]) == {
            "D_MISSED_TRICKS", "D_OVER_DDS_CLAIM", "F_OPENING_LEAD_REGRET"
        }, followups
        assert set(followups["derived_by_type"]) == {"contract_tricks", "opening_lead"}, followups
        assert "regression" in followups["derived_by_evidence_type"], followups
        assert any(x.get("variant_kind", "").startswith("seat_rotation") for x in derived)
        assert all(x.get("transfer_eligible") is False for x in derived)

        sample = dict(tasks[0])
        sample["dealer"] = "N"
        sample["vulnerability"] = "NS"
        rotated = rotate_task(sample, 1)
        assert rotated["dealer"] == "E"
        assert rotated["vulnerability"] == "EW"

        record_skill_check(
            con,
            skill_key="defense.opening_lead",
            task_id="SYM-1",
            deal_id="D-SYM-1",
            evidence_type="symmetry",
            success=True,
            split="derived",
        )
        stored = con.execute(
            "SELECT evidence_type FROM skill_evidence WHERE task_id='SYM-1'"
        ).fetchone()[0]
        assert stored == "reinforcement", stored
        transfer_count = con.execute(
            "SELECT transfer_count FROM skill_profiles WHERE skill_key='defense.opening_lead'"
        ).fetchone()[0]
        assert transfer_count == 0, transfer_count

        record_skill_check(
            con,
            skill_key="defense.opening_lead",
            task_id="TRANSFER-1",
            deal_id="D-TRANSFER-1",
            evidence_type="transfer",
            success=True,
            split="derived",
            details={"transfer_eligible": True, "source": "fresh_corpus"},
        )
        transfer_count = con.execute(
            "SELECT transfer_count FROM skill_profiles WHERE skill_key='defense.opening_lead'"
        ).fetchone()[0]
        assert transfer_count == 1, transfer_count

        record_skill_check(
            con,
            skill_key="defense.opening_lead",
            task_id="REG-1",
            deal_id="D-REG-1",
            evidence_type="regression",
            success=True,
            split="derived",
        )
        regression_passes = con.execute(
            "SELECT regression_passes FROM skill_profiles WHERE skill_key='defense.opening_lead'"
        ).fetchone()[0]
        assert regression_passes == 1, regression_passes

        for i in range(100):
            schedule_spaced_reviews(
                con,
                skill_key="declarer.trick_estimation",
                source_task_id=f"ERR-{i}",
                current_evaluations=1000,
                run_id="selftest",
            )
        queue_rows = con.execute(
            "SELECT COUNT(*) FROM learning_queue WHERE skill_key='declarer.trick_estimation' AND purpose='spaced_review'"
        ).fetchone()[0]
        requested = con.execute(
            "SELECT SUM(requested_tasks) FROM learning_queue WHERE skill_key='declarer.trick_estimation' AND purpose='spaced_review'"
        ).fetchone()[0]
        assert queue_rows == 3, queue_rows
        assert requested == 300, requested

        model_losses = {
            "baseline": {
                "contract_tricks": {f"C{i}": 2.0 for i in range(100)},
                "opening_lead": {f"L{i}": 0.2 for i in range(100)},
            },
            "candidate": {
                "contract_tricks": {f"C{i}": 1.0 for i in range(100)},
                "opening_lead": {f"L{i}": 0.5 for i in range(100)},
            },
        }
        contract_winner, _ = _select_family(
            "contract_tricks", model_losses, "baseline", 0.005, 200
        )
        lead_winner, _ = _select_family(
            "opening_lead", model_losses, "baseline", 0.005, 200
        )
        assert contract_winner == "candidate", contract_winner
        assert lead_winner == "baseline", lead_winner

        print(json.dumps({
            "ok": True,
            "balanced_followup_sources": followups["source_types"],
            "balanced_error_codes": followups["source_error_codes"],
            "derived_by_type": followups["derived_by_type"],
            "regression_and_reinforcement_separated": True,
            "same_source_transfer_count": 0,
            "rotated_metadata_consistent": True,
            "spaced_review_rows_for_100_errors": queue_rows,
            "spaced_review_requested_tasks": requested,
            "family_model_selection": {
                "contract_tricks": contract_winner,
                "opening_lead": lead_winner,
            },
        }, indent=2))


if __name__ == "__main__":
    main()
