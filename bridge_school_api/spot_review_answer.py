"""Versioned spot answer and learner-safe review projection for an authorized store.

This module does not authenticate callers or expose an HTTP route. The existing
preview Store persists the exact response payload in its replay-validated journal.
Legacy responses remain readable; their absent first play is never inferred.
"""

from __future__ import annotations

from copy import deepcopy

from .local_spot_session import load_store_class


ANSWER_SCHEMA = "spot-first-play-response-v2"
REVIEW_SCHEMA = "bridge-await-teacher-review-v1"
MISSING_SCHEMA = "bridge-await-teacher-review-unavailable-v1"
SPOT_IDS = frozenset({"spot.a7c2", "spot.f3b8"})


def _answer(payload, assignment):
    if type(payload) is not dict or set(payload) != {
        "schema", "exercise_id", "prompt_sha256", "first_play",
        "guaranteed_spot_tricks_from_position", "explanation",
    } or payload["schema"] != ANSWER_SCHEMA:
        raise ValueError("SPOT_ANSWER_SHAPE")
    prompt = assignment["prompt"]
    if (prompt["exercise_id"] not in SPOT_IDS or
            payload["exercise_id"] != prompt["exercise_id"] or
            payload["prompt_sha256"] != prompt["prompt_sha256"]):
        raise ValueError("SPOT_ANSWER_BINDING")
    card = payload["first_play"]
    hand = prompt["presentation"]["hand"]
    if (type(card) is not str or len(card) != 2 or
            card[0] not in "SHDC" or card[1] not in "AKQJT98765432" or
            card[1] not in hand.get(card[0], [])):
        raise ValueError("SPOT_FIRST_PLAY_NOT_IN_HAND")
    count = payload["guaranteed_spot_tricks_from_position"]
    if type(count) is not int or count not in (0, 1):
        raise ValueError("SPOT_TRICK_COUNT")
    explanation = payload["explanation"]
    if type(explanation) is not str or not 1 <= len(explanation.strip()) <= 4000:
        raise ValueError("SPOT_EXPLANATION")
    return deepcopy(payload)


def validate_new_answer(store, command):
    """Validate v2 before a new execute; Store's revision check resolves races."""
    payload = command.get("payload") if type(command) is dict else None
    if type(payload) is not dict or payload.get("schema") != ANSWER_SCHEMA:
        return
    if command.get("kind") != "RESPONSE":
        raise ValueError("SPOT_ANSWER_COMMAND")
    current = store.resume()["current_assignment"]
    if not current or current.get("issuance_id") != command.get("issuance_id"):
        raise ValueError("SPOT_ANSWER_ISSUANCE")
    _answer(payload, current)


def project_saved_review(store):
    """Read only from a caller-authorized, replay-validated existing journal."""
    if type(store) is not load_store_class():
        raise ValueError("REVIEW_STORE_TYPE")
    module = __import__(type(store).__module__, fromlist=["SQLiteJournal"])
    with module.SQLiteJournal(store.path) as journal:
        lesson, _, _, pending = store._replay(journal)
        if pending is not None or lesson.current().get("status") != "AWAIT_TEACHER_REVIEW":
            raise ValueError("REVIEW_NOT_ACKNOWLEDGED_TERMINAL")
        assignments = {}
        initial = journal.records[0]["event"]["initial_assignment"]
        assignments[initial["issuance_id"]] = initial
        acknowledged = []
        pending_command = None
        for record in journal.records[1:]:
            event = record["event"]
            if event["kind"] == "COMMAND":
                pending_command = event
            elif event["kind"] == "DELIVERY_ACK":
                command = pending_command["command"]
                result = pending_command["delivery"]["result"]
                acknowledged.append((command, result))
                nxt = result.get("next_assignment")
                if nxt is not None:
                    assignments[nxt["issuance_id"]] = nxt
                pending_command = None
        terminal = [(command, result) for command, result in acknowledged
                    if command["kind"] == "ADVANCE" and
                    result.get("status") == "AWAIT_TEACHER_REVIEW" and
                    result.get("next_assignment") is None]
        if not terminal:
            raise ValueError("REVIEW_TERMINAL_ACK_MISSING")
        issuance_id = terminal[-1][0]["issuance_id"]
        assignment = assignments[issuance_id]
        exercise_id = assignment["prompt"]["exercise_id"]
        if exercise_id not in SPOT_IDS:
            raise ValueError("REVIEW_NOT_SPOT")
        responses = [command["payload"] for command, _ in acknowledged
                     if command["kind"] == "RESPONSE" and command["issuance_id"] == issuance_id]
        if not responses:
            raise ValueError("REVIEW_RESPONSE_ACK_MISSING")
        base = {"status": "AWAIT_TEACHER_REVIEW", "exercise_id": exercise_id,
                "session_id": store.session_id, "issuance_id": issuance_id,
                "response_no": len(responses), "next_assignment": None,
                "can_send_new_command": False}
        last = responses[-1]
        if type(last) is not dict or last.get("schema") != ANSWER_SCHEMA:
            return {"schema": MISSING_SCHEMA, **base,
                    "saved_answer": None, "unavailable_reason": "FIRST_PLAY_NOT_RECORDED"}
        saved = _answer(last, assignment)
        return {"schema": REVIEW_SCHEMA, **base,
                "saved_answer": {key: saved[key] for key in (
                    "first_play", "guaranteed_spot_tricks_from_position", "explanation")}}
