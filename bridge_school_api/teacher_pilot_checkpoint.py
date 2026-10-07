"""Checkpoint codec for the audited private ControlPilotRuntime, with no event replay."""
from __future__ import annotations

from copy import deepcopy
from .teacher_pilot_sessions import PilotError, json_copy, require

STATE_FIELDS = ("_index", "_turn", "_hint", "_answered", "_feedback",
                "_attempts", "_completed", "_records")


class ControlRuntimeFactory:
    """Call loader lazily. Loader must validate the pinned private suite and source files."""
    def __init__(self, loader):
        self.loader = loader

    def create(self):
        # This private dependency is intentionally absent from the public patch.
        from .teacher_control_adapter import ControlPilotRuntime
        return CheckpointRuntime(ControlPilotRuntime(self.loader()))

    def restore(self, checkpoint):
        runtime = self.create()
        runtime.restore(checkpoint)
        return runtime


class CheckpointRuntime:
    def __init__(self, control_runtime):
        self.control = control_runtime

    def view(self):
        return self.control.view()

    def handle(self, request):
        return self.control.handle(request)

    def checkpoint(self):
        lesson = self.control._runtime
        return json_copy({
            "schema": "teacher-pilot-checkpoint-v1",
            "identity": {k: self.view()[k] for k in
                         ("version", "content_sha256", "control_suite_version", "control_suite_sha256")},
            "state": {k: deepcopy(getattr(lesson, k)) for k in STATE_FIELDS},
        })

    def restore(self, checkpoint):
        checkpoint = json_copy(checkpoint)
        fresh = self.checkpoint()
        require(type(checkpoint) is dict and set(checkpoint) == set(fresh)
                and checkpoint["schema"] == fresh["schema"]
                and checkpoint["identity"] == fresh["identity"], "CHECKPOINT_IDENTITY")
        state = checkpoint["state"]
        require(type(state) is dict and set(state) == set(STATE_FIELDS), "CHECKPOINT_FIELDS")
        lesson = self.control._runtime
        tasks = lesson._manifest["tasks"]
        require(type(state["_index"]) is int and 0 <= state["_index"] <= len(tasks)
                and type(state["_turn"]) is int and 0 <= state["_turn"] <= 200
                and type(state["_hint"]) is int and 0 <= state["_hint"] <= 3
                and type(state["_attempts"]) is int and 0 <= state["_attempts"] <= 200
                and type(state["_answered"]) is bool, "CHECKPOINT_COUNTERS")
        require(state["_completed"] == [t["task_id"] for t in tasks[:state["_index"]]],
                "CHECKPOINT_COMPLETION")
        records = state["_records"]
        require(type(records) is dict and set(records) == set(lesson._records),
                "CHECKPOINT_RECORDS")
        total_attempts = 0
        all_turns = []
        previous_completion = -1
        for task_index, task in enumerate(tasks):
            record = records[task["task_id"]]
            initial = lesson._records[task["task_id"]]
            require(type(record) is dict and set(record) == set(initial),
                    "CHECKPOINT_RECORD_FIELDS")
            require(all(record[k] == initial[k] for k in
                        ("task_id", "version", "content_sha256")), "CHECKPOINT_RECORD_IDENTITY")
            require(all(type(record[k]) is list for k in ("attempts", "hints", "disclosures"))
                    and all(type(record[k]) is bool for k in
                            ("assistance_seen", "first_unassisted_success", "correct_after_assistance")),
                    "CHECKPOINT_RECORD_TYPES")
            require(record["first_answer"] == (record["attempts"][0] if record["attempts"] else None),
                    "CHECKPOINT_FIRST_ANSWER")
            require(record["outcome"] in {"UNATTEMPTED", "RETRY", "FIRST_UNASSISTED_CORRECT",
                                         "CORRECT_AFTER_ASSISTANCE", "CORRECT_AFTER_RETRY"},
                    "CHECKPOINT_OUTCOME")
            completed = record["completed_turn"]
            require(completed is None or
                    (type(completed) is int and 0 <= completed < state["_turn"]),
                    "CHECKPOINT_COMPLETED_TURN")
            if task_index > state["_index"]:
                require(record == initial, "CHECKPOINT_FUTURE_TASK")
            attempts, hints = record["attempts"], record["hints"]
            total_attempts += len(attempts)
            require([h.get("level") for h in hints] == list(range(1, len(hints) + 1))
                    and len(hints) <= 3, "CHECKPOINT_HINT_SEQUENCE")
            events = sorted([(a.get("turn"), "answer", a) for a in attempts]
                            + [(h.get("turn"), "hint", h) for h in hints],
                            key=lambda item: item[0] if type(item[0]) is int else -1)
            require([a.get("turn") for a in attempts] == sorted(a.get("turn") for a in attempts)
                    and [h.get("turn") for h in hints] == sorted(h.get("turn") for h in hints),
                    "CHECKPOINT_EVENT_ORDER")
            assistance = False
            hint_levels = []
            expected_disclosures = []
            latest_feedback = None
            for turn, kind, item in events:
                require(type(turn) is int and previous_completion < turn < state["_turn"],
                        "CHECKPOINT_EVENT_TURN")
                all_turns.append(turn)
                require(item.get("version") == initial["version"], "CHECKPOINT_EVENT_VERSION")
                if kind == "hint":
                    require(task["hints_allowed"] and not any(
                        a.get("correct") is True and a.get("turn", state["_turn"]) < turn for a in attempts),
                        "CHECKPOINT_HINT_AFTER_ANSWER")
                    require(item.get("reveals_solution") is (item["level"] == 3),
                            "CHECKPOINT_HINT_REVEAL")
                    hint_levels.append(item["level"])
                    if item["level"] == 3:
                        expected_disclosures.append({"version": initial["version"], "turn": turn,
                                                     "kind": "HINT_REVEAL"})
                    from .text_lesson import hint_feedback
                    latest_feedback = hint_feedback(task, item["level"])
                    assistance = True
                    continue
                from .text_lesson import grade, parse_answer
                require(type(item.get("answer")) is dict, "CHECKPOINT_ATTEMPT")
                expected = grade(task, item["answer"], lesson._manifest["context"],
                                 lesson._manifest["rule_payload"])
                correct = expected["status"] == "CORRECT"
                require(item["answer"] == parse_answer(item["answer"])
                        and item.get("correct") is correct
                        and item.get("hcp_correct") is expected["hcp_feedback"]["correct"]
                        and item.get("call_correct") is expected["auction_feedback"]["correct"]
                        and item.get("assistance_before") is assistance
                        and item.get("hint_levels_before") == hint_levels
                        and item.get("disclosure_before") is bool(expected_disclosures)
                        and item.get("feedback_kind") ==
                            ("POST_CORRECT_EXPLANATION" if correct else "CORRECTIVE_FEEDBACK"),
                        "CHECKPOINT_ATTEMPT_RESULT")
                require(not any(a.get("correct") is True and a.get("turn", state["_turn"]) < turn
                                for a in attempts), "CHECKPOINT_ATTEMPT_AFTER_ANSWER")
                if correct or "rule_id" in expected:
                    expected_disclosures.append({"version": initial["version"], "turn": turn,
                        "kind": "POST_CORRECT_EXPLANATION" if correct else "CORRECTIVE_RULE_FEEDBACK"})
                latest_feedback = expected
                if not correct:
                    assistance = True
            require(record["disclosures"] == expected_disclosures, "CHECKPOINT_DISCLOSURES")
            require(record["assistance_seen"] is assistance, "CHECKPOINT_ASSISTANCE")
            first_success = bool(attempts and attempts[0]["correct"]
                                 and not attempts[0]["assistance_before"])
            last = attempts[-1] if attempts else None
            assisted_correct = bool(last and last["correct"] and last["assistance_before"])
            require(record["first_unassisted_success"] is first_success
                    and record["correct_after_assistance"] is assisted_correct,
                    "CHECKPOINT_SUCCESS_ACCOUNTING")
            outcome = ("UNATTEMPTED" if last is None else
                       "FIRST_UNASSISTED_CORRECT" if first_success else
                       "CORRECT_AFTER_ASSISTANCE" if assisted_correct else
                       "CORRECT_AFTER_RETRY" if last["correct"] else "RETRY")
            require(record["outcome"] == outcome, "CHECKPOINT_OUTCOME_ACCOUNTING")
            if task_index < state["_index"]:
                require(completed is not None and last is not None and last["correct"]
                        and all(turn < completed for turn, _, _ in events),
                        "CHECKPOINT_COMPLETED_TASK")
                all_turns.append(completed)
                previous_completion = completed
            else:
                require(completed is None, "CHECKPOINT_ACTIVE_COMPLETION")
            if task_index == state["_index"]:
                require(state["_hint"] == len(hints)
                        and state["_answered"] is bool(last and last["correct"]),
                        "CHECKPOINT_ACTIVE_STATE")
                feedback = state["_feedback"]
                require((latest_feedback is None and feedback is None) or
                        (latest_feedback is not None and type(feedback) is dict and
                         feedback == latest_feedback), "CHECKPOINT_ACTIVE_FEEDBACK")
        require(state["_attempts"] == total_attempts
                and sorted(all_turns) == list(range(state["_turn"])),
                "CHECKPOINT_GLOBAL_COUNTERS")
        feedback = state["_feedback"]
        require(feedback is None or type(feedback) is dict, "CHECKPOINT_FEEDBACK")
        require(not state["_answered"] or
                (feedback is not None and feedback.get("status") == "CORRECT"),
                "CHECKPOINT_ANSWERED")
        require(state["_index"] < len(tasks) or
                (not state["_answered"] and state["_hint"] == 0 and feedback is None),
                "CHECKPOINT_FINISHED")
        for key, value in state.items():
            setattr(lesson, key, deepcopy(value))
