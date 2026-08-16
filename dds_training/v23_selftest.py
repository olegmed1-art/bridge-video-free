from __future__ import annotations

import json
import tempfile
from pathlib import Path

from v23_core import (
    ALGORITHM_VERSION,
    apply_calibrator,
    assess_rule_candidate,
    audit_information_mask,
    audit_lineage,
    audit_shards,
    audit_stage2_readiness,
    calibration_report,
    crossfit_training_families,
    derive_task,
    deterministic_permutation,
    family_id_for,
    find_counterexamples,
    fit_histogram_calibrator,
    make_continuation_task,
    negative_control_report,
    plan_shards,
    stamp_root_task,
    validate_line_bearing_prediction,
    validate_play_line,
    write_readiness,
)

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def main() -> None:
    base = {
        "task_id": "V23-BASE",
        "deal_id": "V23-DEAL",
        "task_type": "play_decision",
        "split": "train",
        "deal": DEAL,
        "declarer": 2,
        "strain": 4,
        "first_to_play": 0,
        "dealer": "N",
        "vulnerability": "None",
    }

    # Legal line and follow-suit enforcement.
    legal_line = ["SQ", "S8", "SK", "SA", "HA", "H2", "H7", "H3"]
    play = validate_play_line(DEAL, first_to_play=0, strain=4, line=legal_line)
    assert play["ok"] and play["completed_tricks"] if "completed_tricks" in play else True
    assert len(play["completed"]) == 2
    assert play["completed"][0]["winner"] == "W"
    try:
        validate_play_line(DEAL, first_to_play=0, strain=4, line=["SQ", "H7"])
    except ValueError as exc:
        assert "failure to follow suit" in str(exc)
    else:
        raise AssertionError("Illegal revoke was accepted")

    prediction = {
        "task_id": base["task_id"],
        "chosen_card": "SQ",
        "candidates": ["SQ", "SJ", "S6"],
        "reason": "Compare the complete spade candidates before DDS.",
        "line": legal_line,
        "confidence_probability": 0.72,
        "locked": True,
    }
    line_audit = validate_line_bearing_prediction(base, prediction)
    assert line_audit["line_sha256"] and line_audit["confidence_probability"] == 0.72

    # Family lineage and split/fold isolation.
    stamped = stamp_root_task(base)
    derived = derive_task(
        stamped,
        task_id="V23-DERIVED",
        deal_id="V23-DEAL-ROT1",
        evidence_role="reinforcement",
        changes={"split": "derived"},
    )
    assert family_id_for(stamped) == family_id_for(derived)
    lineage_ok = audit_lineage([stamped, derived])
    assert lineage_ok["status"] == "ok", lineage_ok
    leaked = {**stamped, "task_id": "V23-LEAK", "deal_id": "V23-LEAK", "split": "sealed_test", "root_split": "sealed_test"}
    leaked["family_id"] = stamped["family_id"]
    leakage_audit = audit_lineage([stamped, leaked])
    assert leakage_audit["status"] == "error"
    fold = stamped["crossfit_fold"]
    crossfit = crossfit_training_families([stamped, derived], held_out_fold=fold)
    assert stamped["family_id"] in crossfit["held_out_families"]
    assert stamped["family_id"] not in crossfit["training_families"]

    # Continuation task and human-information masking.
    continuation = make_continuation_task(
        base,
        ["SQ", "S8", "SK", "SA"],
        decision_id="V23-CONT",
        information_mode="human",
    )
    assert continuation["next_to_play"] == 3
    assert continuation["actor_side"] == "defense"
    assert continuation["legal_cards"]
    mask_audit = audit_information_mask(continuation)
    assert mask_audit["status"] == "ok", mask_audit
    visible = continuation["visible_information"]
    assert visible["hands"]["W"] is not None
    assert visible["hands"]["N"] is None

    # Calibration is explicit and bounded.
    probabilities = [0.1, 0.2, 0.4, 0.55, 0.7, 0.8, 0.9, 0.95]
    outcomes = [0, 0, 0, 1, 1, 1, 1, 1]
    calibration = calibration_report(probabilities, outcomes, bins=4)
    assert 0 <= calibration["ece"] <= 1
    calibrator = fit_histogram_calibrator(probabilities, outcomes, bins=4)
    calibrated = [apply_calibrator(p, calibrator) for p in probabilities]
    assert all(0 <= p <= 1 for p in calibrated)
    assert calibrated == sorted(calibrated)

    # Family-preserving shards never split descendants.
    tasks = []
    for family_index in range(8):
        source = stamp_root_task({**base, "task_id": f"S{family_index}-A", "deal_id": f"D{family_index}"})
        tasks.append(source)
        tasks.append(
            derive_task(
                source,
                task_id=f"S{family_index}-B",
                deal_id=f"D{family_index}-R",
                evidence_role="regression",
            )
        )
    manifest = plan_shards(tasks, max_tasks=5)
    shard_audit = audit_shards(manifest)
    assert shard_audit["status"] == "ok", shard_audit
    assert manifest["manifest_sha256"]

    # Counterexample requires an action flip plus practical regret.
    counterexamples = find_counterexamples(
        {"SA": 5, "H2": 4},
        [
            {"task_id": "NEAR-1", "distance": 1, "scores": {"SA": 3, "H2": 5}, "change": "swap one honor"},
            {"task_id": "NEAR-2", "distance": 2, "scores": {"SA": 5, "H2": 4}, "change": "irrelevant low-card swap"},
        ],
        source_action="SA",
        minimum_regret=1,
    )
    assert len(counterexamples) == 1 and counterexamples[0]["task_id"] == "NEAR-1"

    # Rule promotion needs independent transfer, counterexamples and regressions.
    evidence = []
    evidence += [{"role": "transfer", "independent": True, "success": True, "regret": 0, "split": "train"} for _ in range(30)]
    evidence += [{"role": "regression", "success": True, "regret": 0, "split": "derived"} for _ in range(10)]
    evidence += [{"role": "counterexample", "success": True, "regret": 0, "split": "derived"} for _ in range(5)]
    rule = assess_rule_candidate({"rule_key": "preserve-entry", "version": 1}, evidence)
    assert rule["status"] == "confirmed", rule
    evidence += [{"role": "real_world", "independent": True, "success": True, "regret": 0, "split": "train"} for _ in range(5)]
    stable_rule = assess_rule_candidate({"rule_key": "preserve-entry", "version": 1}, evidence)
    assert stable_rule["status"] == "stable", stable_rule

    # Negative control must be worse than the real-label model.
    shuffled = deterministic_permutation(list(range(20)), seed=17)
    assert sorted(shuffled) == list(range(20)) and shuffled != list(range(20))
    negative = negative_control_report(0.30, [0.47, 0.51, 0.49], minimum_gap=0.10)
    assert negative["status"] == "ok"

    # Stage 2 remains fail-closed until DDS partial-position/trajectory and the
    # durable sharded workflow are implemented and tested.
    readiness = audit_stage2_readiness()
    assert readiness["status"] == "blocked"
    blockers = {x["capability"] for x in readiness["mass_start_blockers"]}
    assert blockers == {
        "dds_partial_position_adapter",
        "full_play_trajectory_integration",
        "stage2_sharded_workflow",
    }, blockers
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "readiness.json"
        written = write_readiness(path)
        assert path.exists() and written == readiness

    print(
        json.dumps(
            {
                "ok": True,
                "algorithm_version": ALGORITHM_VERSION,
                "legal_line_cards": len(legal_line),
                "lineage": lineage_ok,
                "continuation_position": continuation["position_id"],
                "calibration": calibration,
                "shards": len(manifest["shards"]),
                "counterexamples": counterexamples,
                "rule_status": stable_rule["status"],
                "negative_control": negative,
                "stage2_readiness": readiness,
                "mass_training_started": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
