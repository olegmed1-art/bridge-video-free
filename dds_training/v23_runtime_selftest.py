from __future__ import annotations

import json
import copy
import tempfile
from pathlib import Path

from v23_runtime import (
    ALGORITHM_VERSION,
    apply_calibrator,
    assess_rule_candidate,
    audit_information_mask,
    audit_lineage,
    audit_shards,
    audit_stage2_readiness,
    calibration_report,
    continuation_student_view,
    crossfit_training_families,
    derive_task,
    deterministic_permutation,
    family_id_for,
    find_counterexamples,
    fit_histogram_calibrator,
    make_continuation_task,
    negative_control_report,
    plan_shards,
    parse_deal,
    render_deal,
    stamp_root_task,
    validate_line_bearing_prediction,
    validate_continuation_student_view,
    validate_play_line,
    write_readiness,
)

DEAL = "N:QJ6.K652.J85.T98 873.J97.AT764.Q4 K5.T83.KQ9.A7652 AT942.AQ4.32.KJ3"


def check_student_delivery(base: dict) -> dict:
    """Offline delivery attacks plus hidden-world noninterference checks."""
    prefix = ["SQ", "S8", "SK", "SA"]
    source = make_continuation_task(base, prefix, decision_id="PRIVATE-ID", information_mode="human")
    before = copy.deepcopy(source)
    view = continuation_student_view(source)
    validate_continuation_student_view(json.loads(json.dumps(view)), source_task=source)
    assert source == before and source["original_deal"] == DEAL
    assert "deal" in source and "original_deal" in source and source["legal_cards"]
    assert audit_information_mask(source)["status"] == "ok"
    rejected = 0

    def reject(candidate, *, reference=source):
        nonlocal rejected
        try:
            validate_continuation_student_view(candidate, source_task=reference)
        except ValueError as exc:
            assert "HJ" not in str(exc) and DEAL not in str(exc)
            rejected += 1
        else:
            raise AssertionError("Unsafe student envelope accepted")

    # Passing the internal mask audit is never a delivery authorization.
    reject(source)
    double_dummy = make_continuation_task(base, prefix, decision_id="DDS-ID")
    reject(double_dummy)
    reject(view, reference=double_dummy)
    assert double_dummy["original_deal"] == DEAL
    assert all(hand is not None for hand in double_dummy["visible_information"]["hands"].values())

    # Internal annotations/IDs and even a poisoned cached mask never enter the projection.
    poisoned = copy.deepcopy(source)
    for field in ("metadata", "explanation", "reason", "task_id", "position_id", "deal_id", "prediction_schema"):
        poisoned[field] = {"hidden_card": "HJ", "original_deal": DEAL}
    poisoned["visible_information"] = {"hands": {"E": "HJ"}, "explanation": "HJ"}
    assert continuation_student_view(poisoned) == view
    assert "HJ" not in json.dumps(view)
    assert "deal" not in view and "original_deal" not in view and "task_id" not in view

    # Every object level rejects an added free-text/metadata channel.
    partial = make_continuation_task(base, prefix[:2], decision_id="PARTIAL", information_mode="human")
    partial_view = continuation_student_view(partial)
    paths = [(), ("visible_information",), ("visible_information", "hands"),
             ("visible_information", "current_trick", 0)]
    for path in paths:
        for field in ("metadata", "explanation", "deal", "original_deal", "dds_answer"):
            candidate = copy.deepcopy(partial_view)
            target = candidate
            for key in path:
                target = target[key]
            target[field] = {"hidden_card": "HJ"}
            reject(candidate, reference=partial)
    for field in view:
        candidate = copy.deepcopy(view)
        del candidate[field]
        reject(candidate)
        candidate = copy.deepcopy(view)
        candidate[field] = {"explanation": "HJ"}
        reject(candidate)
    for field in view["visible_information"]:
        candidate = copy.deepcopy(view)
        candidate["visible_information"][field] = {"explanation": "HJ"}
        reject(candidate)

    candidate = copy.deepcopy(view)
    candidate["visible_information"]["visible_hands"] = list("ENSW")
    candidate["visible_information"]["hands"]["E"] = parse_deal(source["deal"])[1]
    reject(candidate)
    candidate = copy.deepcopy(view)
    candidate["legal_cards"].append("HJ")
    reject(candidate)
    candidate = copy.deepcopy(view)
    candidate["declarer"] = True  # bool must never compare equal to integer 1.
    reject(candidate)
    candidate = copy.deepcopy(view)
    candidate["visible_information"]["current_trick"] = [{"seat": "E", "card": "HJ"}]
    reject(candidate)
    for auction in (["HJ"], ["1NT HJ"], [{"call": "PASS", "explanation": "HJ"}], "HJ", ["PASS"] * 321):
        candidate = copy.deepcopy(view)
        candidate["visible_information"]["auction"] = auction
        reject(candidate)

    # A schema-valid substitution must still fail equality to the private source.
    candidate = copy.deepcopy(view)
    candidate["visible_information"]["hands"]["W"][2] = "T2"
    candidate["legal_cards"] = ["DT" if card == "D3" else card for card in candidate["legal_cards"]]
    reject(candidate)
    other = make_continuation_task({**base, "declarer": 1}, [], decision_id="BOOL", information_mode="human")
    candidate = continuation_student_view(other)
    candidate["declarer"] = True
    reject(candidate, reference=other)

    # Dummy is not exposed by untrusted metadata before any public card was played.
    opening = make_continuation_task({**base, "first_to_play": 3, "dummy_revealed": True}, [],
                                     decision_id="OPENING", information_mode="human")
    opening_view = continuation_student_view(opening)
    assert opening_view["visible_information"]["visible_hands"] == ["W"]
    assert opening_view["visible_information"]["hands"]["N"] is None
    with_auction = make_continuation_task({**base, "auction": ["1NT", "PASS"]}, prefix,
                                          decision_id="AUCTION", information_mode="human")
    auction_view = continuation_student_view(with_auction)
    validate_continuation_student_view(auction_view, source_task=with_auction)
    auction_view["visible_information"]["auction"].append("PASS")
    reject(auction_view, reference=with_auction)
    assert with_auction["auction"] == ["1NT", "PASS"]
    for auction in ([{"call": "PASS", "explanation": "HJ"}], ["HJ"], "HJ"):
        try:
            continuation_student_view({**source, "auction": auction})
        except ValueError:
            pass
        else:
            raise AssertionError("Unstructured source auction escaped into delivery")

    # Independent hidden-world perturbation: every remaining E/S card swap leaves
    # the complete student envelope identical, including any newly added fields.
    hands = parse_deal(DEAL)
    hidden_cards = {seat: [suit + rank for suit, ranks in zip("SHDC", hands[seat]) for rank in ranks
                          if suit + rank not in prefix] for seat in (1, 2)}
    worlds = 0
    for east in hidden_cards[1]:
        for south in hidden_cards[2]:
            changed = copy.deepcopy(hands)
            for seat, removed, added in ((1, east, south), (2, south, east)):
                index = "SHDC".index(removed[0])
                changed[seat][index] = changed[seat][index].replace(removed[1], "")
                changed[seat]["SHDC".index(added[0])] += added[1]
            alternate = make_continuation_task({**base, "deal": render_deal(changed), "deal_id": "ANOTHER-PRIVATE-ID"},
                prefix, decision_id="ANOTHER-PRIVATE-TASK", information_mode="human")
            assert continuation_student_view(alternate) == view
            worlds += 1
    assert worlds == 144

    # Exercise all seats and trick positions through a full legal offline play.
    public_line = []
    projections = 0
    for _ in range(52):
        for declarer in range(4):
            task = make_continuation_task({**base, "declarer": declarer}, public_line,
                decision_id="OFFLINE", information_mode="human")
            projected = continuation_student_view(task)
            validate_continuation_student_view(projected, source_task=task)
            projections += 1
        public_line.append(task["legal_cards"][0])
    terminal = make_continuation_task(base, public_line, decision_id="TERMINAL", information_mode="human")
    try:
        continuation_student_view(terminal)
    except ValueError:
        pass
    else:
        raise AssertionError("Terminal position was issued as a decision")
    # Mutating the result cannot mutate any private source container.
    view["visible_information"]["hands"]["W"][0] = ""
    view["visible_information"]["public_play"].append("HJ")
    assert source == before
    return {"rejected_envelopes": rejected, "hidden_world_swaps": worlds, "legal_projections": projections}


def check_student_serialization_boundary(private_task: dict) -> dict:
    """Test the existing runtime consumer through JSON; learner delivery is not wired."""
    before = copy.deepcopy(private_task)
    emitted = []

    def emit_json(candidate: dict) -> None:
        # Test-only sink, not an endpoint. Check the final object immediately
        # before serialization; never append unvalidated metadata afterwards.
        validate_continuation_student_view(candidate, source_task=private_task)
        emitted.append(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    envelope = continuation_student_view(private_task)
    emit_json(envelope)
    wire = emitted[0]
    decoded = json.loads(wire)
    validate_continuation_student_view(decoded, source_task=private_task)
    assert decoded == envelope and private_task == before
    assert b'"deal"' not in wire and b'"original_deal"' not in wire
    assert b'"task_id"' not in wire and b"HJ" not in wire

    decorated = copy.deepcopy(envelope)
    decorated["metadata"] = {"explanation": "HJ"}
    nested = copy.deepcopy(envelope)
    nested["visible_information"]["explanation"] = "HJ"
    wrapped = {"payload": envelope, "explanation": "HJ"}
    substituted = copy.deepcopy(envelope)
    substituted["visible_information"]["hands"]["W"][2] = "T2"
    substituted["legal_cards"] = ["DT" if card == "D3" else card for card in substituted["legal_cards"]]
    attempts = (private_task, decorated, nested, wrapped, substituted)
    assert audit_information_mask(private_task)["status"] == "ok"
    for candidate in attempts:
        try:
            emit_json(candidate)
        except ValueError as exc:
            assert "HJ" not in str(exc) and DEAL not in str(exc)
        else:
            raise AssertionError("Unsafe bytes reached the student fixture sink")
        assert emitted == [wire]

    # Keep the full position available to the existing private consumer.
    assert private_task == before and private_task["original_deal"] == DEAL
    assert parse_deal(private_task["deal"])[1]
    internal = validate_line_bearing_prediction(private_task, {
        "line": [private_task["legal_cards"][0]],
        "candidates": [private_task["legal_cards"][0]],
    })
    assert internal["ok"] and private_task == before
    return {"learner_delivery": "not-wired", "serialized_safe_envelopes": len(emitted),
            "rejected_before_serialization": len(attempts), "private_consumer_preserved": True}


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
    student_delivery = check_student_delivery(base)

    legal_line = ["SQ", "S8", "SK", "SA", "HA", "H2", "H7", "H3"]
    play = validate_play_line(DEAL, first_to_play=0, strain=4, line=legal_line)
    assert play["ok"] and len(play["completed"]) == 2
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
        "reason": "Compare all serious spade candidates before DDS.",
        "line": legal_line,
        "confidence_probability": 0.72,
        "locked": True,
    }
    line_audit = validate_line_bearing_prediction(base, prediction)
    assert line_audit["line_sha256"] and line_audit["confidence_probability"] == 0.72

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
    leaked = {
        **stamped,
        "task_id": "V23-LEAK",
        "deal_id": "V23-LEAK",
        "split": "sealed_test",
        "root_split": "sealed_test",
        "family_id": stamped["family_id"],
    }
    assert audit_lineage([stamped, leaked])["status"] == "error"
    fold = stamped["crossfit_fold"]
    crossfit = crossfit_training_families([stamped, derived], held_out_fold=fold)
    assert stamped["family_id"] in crossfit["held_out_families"]
    assert stamped["family_id"] not in crossfit["training_families"]

    continuation = make_continuation_task(
        base,
        ["SQ", "S8", "SK", "SA"],
        decision_id="V23-CONT",
        information_mode="human",
    )
    assert continuation["next_to_play"] == 3
    assert continuation["actor_side"] == "defense"
    assert continuation["legal_cards"]
    assert audit_information_mask(continuation)["status"] == "ok"
    visible = continuation["visible_information"]
    # West sees own hand and the revealed North dummy, but not East/South.
    assert visible["hands"]["W"] is not None
    assert visible["hands"]["N"] is not None
    assert visible["hands"]["E"] is None
    assert visible["hands"]["S"] is None
    serialization_boundary = check_student_serialization_boundary(continuation)

    probabilities = [0.1, 0.2, 0.4, 0.55, 0.7, 0.8, 0.9, 0.95]
    outcomes = [0, 0, 0, 1, 1, 1, 1, 1]
    calibration = calibration_report(probabilities, outcomes, bins=4)
    calibrator = fit_histogram_calibrator(probabilities, outcomes, bins=4)
    calibrated = [apply_calibrator(p, calibrator) for p in probabilities]
    assert 0 <= calibration["ece"] <= 1
    assert calibrated == sorted(calibrated)

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
    assert audit_shards(manifest)["status"] == "ok"

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

    evidence = []
    evidence += [{"role": "transfer", "independent": True, "success": True, "regret": 0, "split": "train"} for _ in range(30)]
    evidence += [{"role": "regression", "success": True, "regret": 0, "split": "derived"} for _ in range(10)]
    evidence += [{"role": "counterexample", "success": True, "regret": 0, "split": "derived"} for _ in range(5)]
    assert assess_rule_candidate({"rule_key": "preserve-entry", "version": 1}, evidence)["status"] == "confirmed"
    evidence += [{"role": "real_world", "independent": True, "success": True, "regret": 0, "split": "train"} for _ in range(5)]
    stable_rule = assess_rule_candidate({"rule_key": "preserve-entry", "version": 1}, evidence)
    assert stable_rule["status"] == "stable"

    shuffled = deterministic_permutation(list(range(20)), seed=17)
    assert sorted(shuffled) == list(range(20)) and shuffled != list(range(20))
    negative = negative_control_report(0.30, [0.47, 0.51, 0.49], minimum_gap=0.10)
    assert negative["status"] == "ok"

    readiness = audit_stage2_readiness()
    blockers = {row["capability"] for row in readiness["mass_start_blockers"]}
    assert readiness["status"] == "blocked"
    assert blockers == {
        "dds_partial_position_adapter",
        "full_play_trajectory_integration",
        "stage2_sharded_workflow",
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "readiness.json"
        assert write_readiness(path) == readiness and path.exists()

    print(
        json.dumps(
            {
                "ok": True,
                "algorithm_version": ALGORITHM_VERSION,
                "legal_line_cards": len(legal_line),
                "continuation_position": continuation["position_id"],
                "calibration_ece": calibration["ece"],
                "shards": len(manifest["shards"]),
                "counterexamples": len(counterexamples),
                "rule_status": stable_rule["status"],
                "stage2_readiness": readiness["status"],
                "mass_training_started": False,
                "student_delivery": student_delivery,
                "serialization_boundary": serialization_boundary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
