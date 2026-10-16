"""An offline, teacher-issued session through the actual loopback HTTP route."""

import importlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge_school_api.local_spot_preview import register_local_spot_preview
from bridge_school_api.local_spot_session import load_store_class


SOURCE_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("spot_id,first_play,count", [
    ("spot.a7c2", "DA", 1),
    ("spot.f3b8", "CA", 0),
])
def test_store_route_recovery_and_teacher_review(monkeypatch, tmp_path, spot_id, first_play, count):
    assert (SOURCE_ROOT / "evolutionary_course" / "teacher_session_store.py").is_file()
    monkeypatch.setenv("BRIDGE_LOCAL_SPOT_PREVIEW", "1")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    journal = tmp_path / "lesson.sqlite3"
    monkeypatch.setenv("BRIDGE_LOCAL_TEACHER_JOURNAL", str(journal))
    monkeypatch.setenv("BRIDGE_LOCAL_TEACHER_SESSION_ID", "local-route-test")
    store = load_store_class().create(journal, "local-route-test", preview_exercise_id=spot_id)
    assert store.resume()["current_assignment"]["prompt"]["exercise_id"] == "entry.e1"

    app = FastAPI()
    register_local_spot_preview(app)
    client = TestClient(app, client=("127.0.0.1", 50000))
    endpoint = "/local/spot-cycle/session/transport"
    saved_url = "/local/spot-cycle/session/saved-answer"
    assert TestClient(app, client=("192.0.2.1", 50000)).post(
        endpoint, json={"operation": "resume", "value": {}}).status_code == 404
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert client.post(endpoint, json={"operation": "resume", "value": {}}).status_code == 404
    monkeypatch.delenv("VERCEL_ENV")

    def call(operation, value):
        response = client.post(endpoint, json={"operation": operation, "value": value})
        assert response.status_code == 200, response.text
        return response.json()

    assert client.get("/local/spot-cycle/session/").status_code == 200
    assert client.get("/local/spot-cycle/session/session-host.js").status_code == 200
    assert client.get("/local/spot-cycle/session/learner-presentation.json").status_code == 200
    assert client.get("/local/spot-cycle/session/secret").status_code == 404
    assert client.get(saved_url).status_code == 409
    state = call("resume", {})
    assert state["revision"] == 1
    assert state["acknowledged_history"]["rows"]
    assignment = state["current_assignment"]
    assert assignment["prompt"]["exercise_id"] == "entry.e1"
    assert "expected_solution" not in str(assignment)

    step = importlib.import_module("_local_spot_candidate.teaching_step")
    expected = step.exercise_version_fields("entry.e1")["exercise_version"]["expected_solution"]["expected_answers"]
    response = {"schema": "low-card-structured-response-v1", "exercise_id": "entry.e1",
                "prompt_sha256": assignment["prompt"]["prompt_sha256"], "answers": expected,
                "explanation": "Проверка сохранения и выбранного учителем следующего упражнения."}
    command = {"command_id": "response-e1", "kind": "RESPONSE", "issuance_id": assignment["issuance_id"], "payload": response}
    delivery = call("execute", {"command": command, "expected_revision": state["revision"]})
    assert delivery["result"]["status"] == "RECORDED_PENDING_FEEDBACK"
    assert call("execute", {"command": command, "expected_revision": 1}) == delivery
    assert call("resume", {})["pending_delivery"]["delivery_id"] == "response-e1"
    ack = {"delivery_id": delivery["delivery_id"], "result_sha256": delivery["result_sha256"]}
    assert call("acknowledge", ack) == call("acknowledge", ack)
    resumed = call("resume", {})
    assert resumed["acknowledged_history"]["rows"][0]["deliveries"][0]["kind"] == "RESPONSE"

    advance = {"command_id": "advance-e1", "kind": "ADVANCE", "issuance_id": assignment["issuance_id"], "payload": None}
    delivery = call("execute", {"command": advance, "expected_revision": resumed["revision"]})
    assert delivery["result"]["status"] == "NEXT_ACTIVITY"
    assert delivery["result"]["next_assignment"]["prompt"]["exercise_id"] == spot_id
    call("acknowledge", {"delivery_id": delivery["delivery_id"], "result_sha256": delivery["result_sha256"]})
    spot = call("resume", {})["current_assignment"]
    assert spot["prompt"]["exercise_id"] == spot_id

    response = {"schema": "spot-first-play-response-v2", "exercise_id": spot_id,
                "prompt_sha256": spot["prompt"]["prompt_sha256"], "first_play": first_play,
                "guaranteed_spot_tricks_from_position": count,
                "explanation": "Проверка сохранения ответа до педагогического разбора."}
    spot_command = {"command_id": "response-spot", "kind": "RESPONSE", "issuance_id": spot["issuance_id"], "payload": response}
    delivery = call("execute", {"command": spot_command, "expected_revision": call("resume", {})["revision"]})
    call("acknowledge", {"delivery_id": delivery["delivery_id"], "result_sha256": delivery["result_sha256"]})
    advance = {"command_id": "advance-spot", "kind": "ADVANCE", "issuance_id": spot["issuance_id"], "payload": None}
    delivery = call("execute", {"command": advance, "expected_revision": call("resume", {})["revision"]})
    assert delivery["result"]["status"] == "AWAIT_TEACHER_REVIEW"
    assert delivery["result"]["next_assignment"] is None
    assert client.get(saved_url).status_code == 409
    call("acknowledge", {"delivery_id": delivery["delivery_id"], "result_sha256": delivery["result_sha256"]})
    final = call("resume", {})
    assert final["current_assignment"]["status"] == "AWAIT_TEACHER_REVIEW"
    assert final["acknowledged_history"]["rows"][-1]["deliveries"][-1]["kind"] == "ADVANCE"
    assert final["can_send_new_command"] is False
    saved = client.get(saved_url)
    assert saved.status_code == 200
    assert saved.headers["cache-control"] == "no-store"
    assert saved.json() == {
        "schema": "bridge-await-teacher-review-v1",
        "status": "AWAIT_TEACHER_REVIEW", "exercise_id": spot_id,
        "session_id": "local-route-test", "issuance_id": spot["issuance_id"],
        "response_no": 1, "next_assignment": None, "can_send_new_command": False,
        "saved_answer": {"first_play": first_play,
                         "guaranteed_spot_tricks_from_position": count,
                         "explanation": response["explanation"]},
    }
    assert client.get(saved_url).json() == saved.json()
