from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from config import ALGORITHM_VERSION, FOLLOWUP_SOURCE_POLICY
from storage import connect


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latest_investigation_details(con: sqlite3.Connection) -> list[dict]:
    try:
        rows = con.execute(
            """
            SELECT i.event_type,i.details_json
            FROM investigation_events i
            JOIN (SELECT task_id,MAX(id) AS max_id FROM investigation_events GROUP BY task_id) x ON x.max_id=i.id
            WHERE i.event_type='resolved'
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [json.loads(details) for _, details in rows]


def _confidence_diagnostics(con: sqlite3.Connection) -> dict:
    rows = con.execute(
        """
        SELECT p.task_type,p.split,p.prediction_json,r.result_json
        FROM predictions p JOIN dds_results r ON r.task_id=p.task_id
        WHERE p.split IN ('validation','sealed_test')
        """
    ).fetchall()
    stats: dict[tuple[str, str], list[float]] = defaultdict(list)
    for task_type, split, pred_json, result_json in rows:
        pred, result = json.loads(pred_json), json.loads(result_json)
        confidence = str(pred.get("confidence", "unknown")).lower()
        if task_type == "contract_tricks":
            loss = abs(int(pred["tricks"]) - int(result["dds_tricks"]))
        else:
            regret = result.get("dd_regret")
            loss = 13.0 if regret is None else float(regret)
        stats[(task_type, confidence)].append(float(loss))
    out = {}
    for (task_type, confidence), losses in sorted(stats.items()):
        out.setdefault(task_type, {})[confidence] = {
            "n": len(losses),
            "mean_loss": sum(losses) / len(losses),
            "zero_loss_rate": sum(x == 0 for x in losses) / len(losses),
        }
    return out


def _derived_stats(rows: list[dict], base_by_id: dict[str, dict]) -> tuple[Counter, Counter, Counter]:
    types = Counter(str(x.get("task_type")) for x in rows)
    groups = Counter(
        (
            str(x.get("source_task_type", base_by_id.get(x.get("derived_from_task_id"), {}).get("task_type", "unknown"))),
            str(x.get("source_error_code", "unknown")),
        )
        for x in rows
    )
    evidence = Counter(str(x.get("evidence_type", "unknown")) for x in rows)
    return types, groups, evidence


def audit_methodology(work: Path) -> dict:
    """Audit current-revision methodology without rewriting historical evidence.

    Old pilot defects remain visible as warnings/postmortem facts.  They no longer
    block a corrected later revision merely because immutable v2.1/v2.2 evidence
    still exists.  Only violations produced under the active policy/version are
    expansion blockers.
    """
    con = connect(work / "training.sqlite3")
    findings: list[dict] = []

    def add(code: str, severity: str, detail: str, count: int | None = None, scope: str = "current") -> None:
        item = {"code": code, "severity": severity, "detail": detail, "scope": scope}
        if count is not None:
            item["count"] = int(count)
        findings.append(item)

    base_tasks = _load_jsonl(work / "blind_tasks.jsonl")
    base_by_id = {x["task_id"]: x for x in base_tasks}
    all_derived = _load_jsonl(work / "derived_blind_tasks.jsonl")
    current_derived = [
        x for x in all_derived
        if str(x.get("source_selection_policy", "")) == FOLLOWUP_SOURCE_POLICY
    ]
    legacy_derived = [x for x in all_derived if x not in current_derived]

    train_error_groups = Counter()
    for task_id, result_json in con.execute(
        "SELECT task_id,result_json FROM dds_results WHERE split='train'"
    ):
        result = json.loads(result_json)
        code = str(result.get("error_code", "UNKNOWN"))
        if code == "OK":
            continue
        task = base_by_id.get(task_id)
        if task:
            train_error_groups[(task["task_type"], code)] += 1

    derived_types, derived_source_groups, derived_evidence = _derived_stats(current_derived, base_by_id)
    legacy_types, legacy_groups, legacy_evidence = _derived_stats(legacy_derived, base_by_id)

    if current_derived:
        required_types = {task_type for task_type, _ in train_error_groups}
        missing_types = sorted(required_types - set(derived_types))
        if missing_types:
            add(
                "DERIVED_TASK_TYPE_IMBALANCE",
                "error",
                f"Current-policy follow-ups omit task types with TRAIN errors: {missing_types}.",
                len(missing_types),
            )
        required_groups = set(train_error_groups)
        present_groups = set(derived_source_groups)
        missing_groups = sorted(required_groups - present_groups)
        if missing_groups:
            add(
                "DERIVED_ERROR_FAMILY_GAPS",
                "error",
                f"Current-policy follow-ups omit TRAIN error families: {missing_groups}.",
                len(missing_groups),
            )
        add(
            "DERIVED_SAMPLE_IS_TARGETED",
            "info",
            "Current derived metrics are adversarial TRAIN-error neighborhoods and require a matched baseline.",
            len(current_derived),
        )
    elif train_error_groups:
        add(
            "NO_CURRENT_POLICY_FOLLOWUP_SAMPLE",
            "warning",
            "No follow-up file from the active balanced policy exists yet; Stage-2 readiness preflight must generate one before skill claims.",
            0,
        )

    if legacy_derived:
        add(
            "LEGACY_DERIVED_SAMPLE_RETAINED",
            "warning",
            "Historical pilot derived tasks are retained immutably for audit but are not used to judge the active follow-up generator.",
            len(legacy_derived),
            scope="historical",
        )

    current_bad_transfer = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence
        WHERE evidence_type IN ('symmetry','perturbation') AND algorithm_version=?
        """,
        (ALGORITHM_VERSION,),
    ).fetchone()[0]
    if current_bad_transfer:
        add(
            "SAME_SOURCE_PROBES_COUNTED_AS_TRANSFER",
            "error",
            "The active revision stored same-source symmetry/perturbation as transfer-like evidence; use reinforcement instead.",
            current_bad_transfer,
        )
    historical_bad_transfer = con.execute(
        """
        SELECT COUNT(*) FROM skill_evidence
        WHERE evidence_type IN ('symmetry','perturbation') AND algorithm_version<>?
        """,
        (ALGORITHM_VERSION,),
    ).fetchone()[0]
    if historical_bad_transfer:
        add(
            "HISTORICAL_SAME_SOURCE_TRANSFER",
            "warning",
            "Older immutable revisions contain symmetry/perturbation evidence under legacy semantics; current skill promotion excludes it.",
            historical_bad_transfer,
            scope="historical",
        )

    counterexamples = con.execute("SELECT COUNT(*) FROM counterexamples").fetchone()[0]
    if counterexamples == 0:
        add(
            "NO_VERIFIED_COUNTEREXAMPLES",
            "warning",
            "No verified counterexamples exist; stable-skill claims remain blocked by the separate claim gate.",
            0,
        )

    rules = con.execute("SELECT COUNT(*) FROM rule_versions").fetchone()[0]
    if rules == 0:
        add(
            "NO_VERSIONED_BRIDGE_RULES",
            "warning",
            "No recurring error has yet become an independently tested versioned bridge rule.",
            0,
        )

    error_events = con.execute("SELECT COUNT(*) FROM error_events").fetchone()[0]
    review_queue = con.execute(
        "SELECT COUNT(*) FROM learning_queue WHERE purpose='spaced_review' AND status='planned'"
    ).fetchone()[0]
    review_ratio = review_queue / max(1, error_events)
    if review_ratio > 3.2:
        add(
            "HISTORICAL_SPACED_REVIEW_QUEUE_EXPANSION",
            "warning",
            f"Existing queue has {review_ratio:.2f} rows per error. Active revisions aggregate future requests; historical rows remain auditable.",
            review_queue,
            scope="historical",
        )

    resolution_details = _latest_investigation_details(con)
    structural = 0
    card_line = 0
    for details in resolution_details:
        quality = str((details.get("evidence") or {}).get("resolution_quality", "unknown"))
        if quality.startswith("structural_"):
            structural += 1
        elif "card" in quality or "line" in quality:
            card_line += 1
    if structural:
        add(
            "HISTORICAL_STRUCTURAL_OVERCLAIM_RESOLUTION",
            "warning",
            "Older overclaims without a proposed line were honestly closed at position level; new line-bearing tasks permit card-level refutation.",
            structural,
            scope="historical",
        )

    trajectories = con.execute(
        "SELECT COUNT(*) FROM experience_events WHERE event_type='value_trajectory'"
    ).fetchone()[0]
    if trajectories == 0:
        add(
            "NO_RECORDED_FULL_PLAY_TRAJECTORIES",
            "warning",
            "The completed pilot contains no persisted full-play trajectory; Stage 2 preflight now tests the trajectory engine before mass use.",
            0,
        )

    comparison_path = work / "validation_model_comparison.json"
    if comparison_path.exists():
        comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
        if "family_selection" not in comparison:
            add(
                "HISTORICAL_SINGLE_SCORE_MODEL_SELECTION",
                "warning",
                "The pilot used a legacy combined score; active policy uses family-specific paired bootstrap selection.",
                scope="historical",
            )

    confidence = _confidence_diagnostics(con)
    for task_type, levels in confidence.items():
        if "low" in levels and "medium" in levels:
            if levels["medium"]["mean_loss"] > levels["low"]["mean_loss"] + 1e-9:
                add(
                    "CONFIDENCE_NOT_YET_CALIBRATED",
                    "warning",
                    f"For {task_type}, medium-confidence mean loss ({levels['medium']['mean_loss']:.3f}) exceeds low-confidence loss ({levels['low']['mean_loss']:.3f}); the holdout gate requires OOF calibration.",
                )

    status = "error" if any(x["severity"] == "error" for x in findings) else "ok"
    return {
        "methodology_version": ALGORITHM_VERSION,
        "active_followup_policy": FOLLOWUP_SOURCE_POLICY,
        "status": status,
        "advance_allowed": status == "ok",
        "train_error_groups": {
            f"{task_type}:{code}": count for (task_type, code), count in sorted(train_error_groups.items())
        },
        "current_derived_by_type": dict(sorted(derived_types.items())),
        "current_derived_by_source_group": {
            f"{task_type}:{code}": count for (task_type, code), count in sorted(derived_source_groups.items())
        },
        "current_derived_by_evidence": dict(sorted(derived_evidence.items())),
        "legacy_derived_by_type": dict(sorted(legacy_types.items())),
        "legacy_derived_by_source_group": {
            f"{task_type}:{code}": count for (task_type, code), count in sorted(legacy_groups.items())
        },
        "legacy_derived_by_evidence": dict(sorted(legacy_evidence.items())),
        "counterexamples": counterexamples,
        "rule_versions": rules,
        "spaced_review_queue": review_queue,
        "spaced_review_per_error": review_ratio,
        "structural_investigation_resolutions": structural,
        "card_line_investigation_resolutions": card_line,
        "value_trajectories": trajectories,
        "confidence_diagnostics": confidence,
        "findings": findings,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Audit active DDS methodology separately from immutable history")
    p.add_argument("--work", required=True)
    p.add_argument("--out")
    p.add_argument("--fail-on-error", action="store_true")
    args = p.parse_args()
    report = audit_methodology(Path(args.work))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    if args.fail_on_error and report["status"] != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
