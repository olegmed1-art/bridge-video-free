"""Synthetic local Store contract; no browser identity or production data."""

import importlib

import pytest

from bridge_school_api.local_spot_session import apply_operation_to_store, load_store_class
from bridge_school_api.spot_review_answer import (
    ANSWER_SCHEMA, MISSING_SCHEMA, REVIEW_SCHEMA, project_saved_review,
)


def send(store, kind, issuance, payload=None, *, command_id=None):
    command = {"command_id": command_id or f"{kind}-{store.resume()['revision']}",
               "kind": kind, "issuance_id": issuance, "payload": payload}
    result = apply_operation_to_store(store, "execute", {
        "command": command, "expected_revision": store.resume()["revision"]})
    return result


def ack(store, delivery):
    return store.acknowledge(delivery["delivery_id"], delivery["result_sha256"])


def spot_store(tmp_path):
    store = load_store_class().create(tmp_path / "spot.sqlite3", "review-test", preview_exercise_id="spot.a7c2")
    entry = store.resume()["current_assignment"]
    step = importlib.import_module("_local_spot_candidate.teaching_step")
    expected = step.exercise_version_fields("entry.e1")["exercise_version"]["expected_solution"]["expected_answers"]
    legacy = {"schema": "low-card-structured-response-v1", "exercise_id": "entry.e1",
              "prompt_sha256": entry["prompt"]["prompt_sha256"], "answers": expected,
              "explanation": "A synthetic entry response for local review testing."}
    ack(store, send(store, "RESPONSE", entry["issuance_id"], legacy))
    ack(store, send(store, "ADVANCE", entry["issuance_id"]))
    spot = store.resume()["current_assignment"]
    assert spot["prompt"]["exercise_id"] == "spot.a7c2"
    return store, spot


def answer(spot, *, first_play=None, count=1, explanation="My own synthetic reasoning."):
    first_play = first_play or "DA"
    return {"schema": ANSWER_SCHEMA, "exercise_id": "spot.a7c2",
            "prompt_sha256": spot["prompt"]["prompt_sha256"], "first_play": first_play,
            "guaranteed_spot_tricks_from_position": count, "explanation": explanation}


def test_v2_answer_is_saved_and_projected_only_after_terminal_ack(tmp_path):
    store, spot = spot_store(tmp_path)
    payload = answer(spot)
    with pytest.raises(ValueError, match="REVIEW_NOT_ACKNOWLEDGED_TERMINAL"):
        project_saved_review(store)
    response = send(store, "RESPONSE", spot["issuance_id"], payload)
    assert response["result"]["status"] == "RECORDED_PENDING_FEEDBACK"
    ack(store, response)
    advance = send(store, "ADVANCE", spot["issuance_id"])
    assert advance["result"]["status"] == "AWAIT_TEACHER_REVIEW"
    assert advance["result"]["next_assignment"] is None
    with pytest.raises(ValueError, match="REVIEW_NOT_ACKNOWLEDGED_TERMINAL"):
        project_saved_review(store)
    ack(store, advance)
    review = project_saved_review(load_store_class()(store.path, store.session_id))
    assert review == {"schema": REVIEW_SCHEMA, "status": "AWAIT_TEACHER_REVIEW",
                      "exercise_id": "spot.a7c2", "session_id": "review-test",
                      "issuance_id": spot["issuance_id"], "response_no": 1,
                      "next_assignment": None, "can_send_new_command": False,
                      "saved_answer": {"first_play": payload["first_play"],
                                       "guaranteed_spot_tricks_from_position": 1,
                                       "explanation": payload["explanation"]}}
    assert "expected_solution" not in str(review)
    assert "journal" not in str(review)


def test_last_acknowledged_revision_and_legacy_without_invented_first_play(tmp_path):
    store, spot = spot_store(tmp_path)
    legacy = {"schema": "low-card-structured-response-v1", "exercise_id": "spot.a7c2",
              "prompt_sha256": spot["prompt"]["prompt_sha256"],
              "answers": {"visible_count": 3, "missing_count": 2,
                          "guaranteed_spot_tricks_from_position": 1},
              "explanation": "Legacy answer lacks a first play."}
    ack(store, send(store, "RESPONSE", spot["issuance_id"], legacy))
    first = answer(spot, explanation="First v2 response.")
    ack(store, send(store, "RESPONSE", spot["issuance_id"], first))
    final = answer(spot, count=0, explanation="Revised count, still my own answer.")
    ack(store, send(store, "RESPONSE", spot["issuance_id"], final))
    ack(store, send(store, "ADVANCE", spot["issuance_id"]))
    projected = project_saved_review(store)
    assert projected["response_no"] == 3
    assert projected["saved_answer"]["explanation"] == final["explanation"]
    assert projected["saved_answer"]["guaranteed_spot_tricks_from_position"] == 0

    (tmp_path / "old").mkdir()
    old_store, old_spot = spot_store(tmp_path / "old")
    ack(old_store, send(old_store, "RESPONSE", old_spot["issuance_id"], legacy |
                        {"prompt_sha256": old_spot["prompt"]["prompt_sha256"]}))
    ack(old_store, send(old_store, "ADVANCE", old_spot["issuance_id"]))
    old = project_saved_review(old_store)
    assert old["schema"] == MISSING_SCHEMA
    assert old["saved_answer"] is None
    assert old["unavailable_reason"] == "FIRST_PLAY_NOT_RECORDED"


def test_other_visible_first_play_remains_valid_for_manual_review(tmp_path):
    store, spot = spot_store(tmp_path)
    ack(store, send(store, "RESPONSE", spot["issuance_id"], answer(spot, first_play="S3")))
    ack(store, send(store, "ADVANCE", spot["issuance_id"]))
    assert project_saved_review(store)["saved_answer"]["first_play"] == "S3"


@pytest.mark.parametrize("change", [
    {"first_play": "SA"}, {"first_play": "INVALID"},
    {"guaranteed_spot_tricks_from_position": True},
    {"guaranteed_spot_tricks_from_position": 2},
    {"explanation": " "}, {"exercise_id": "spot.f3b8"},
])
def test_rejects_invalid_new_answer_before_journal_append(tmp_path, change):
    store, spot = spot_store(tmp_path)
    payload = answer(spot) | change
    revision = store.resume()["revision"]
    with pytest.raises(ValueError):
        send(store, "RESPONSE", spot["issuance_id"], payload)
    assert store.resume()["revision"] == revision
