"""Synchronous route-function checks when the Windows ASGI event loop is unavailable.

These do not replace HTTP TestClient coverage: dependency injection and ASGI
response delivery require a working event loop.
"""

import importlib

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from bridge_school_api.local_spot_preview import register_local_spot_preview
from bridge_school_api.local_spot_session import apply_operation_to_store, load_store_class
import bridge_school_api.school_spot_lesson as lesson


def request(*, host="127.0.0.1", headers=()):
    return Request({"type": "http", "method": "GET", "path": "/",
                    "scheme": "http", "headers": [(key.lower().encode(), value.encode())
                                                   for key, value in headers],
                    "client": (host, 50000), "server": ("testserver", 80)})


def submit(store, kind, issuance, payload=None):
    return apply_operation_to_store(store, "execute", {
        "command": {"command_id": f"{kind}-{store.resume()['revision']}",
                    "kind": kind, "issuance_id": issuance, "payload": payload},
        "expected_revision": store.resume()["revision"]})


def ack(store, delivery):
    store.acknowledge(delivery["delivery_id"], delivery["result_sha256"])


def spot_store(tmp_path):
    store = load_store_class().create(tmp_path / "route.sqlite3", "route-session",
                                      preview_exercise_id="spot.a7c2")
    entry = store.resume()["current_assignment"]
    step = importlib.import_module("_local_spot_candidate.teaching_step")
    expected = step.exercise_version_fields("entry.e1")["exercise_version"]["expected_solution"]["expected_answers"]
    ack(store, submit(store, "RESPONSE", entry["issuance_id"], {
        "schema": "low-card-structured-response-v1", "exercise_id": "entry.e1",
        "prompt_sha256": entry["prompt"]["prompt_sha256"],
        "answers": expected, "explanation": "Synthetic entry answer."}))
    ack(store, submit(store, "ADVANCE", entry["issuance_id"]))
    spot = store.resume()["current_assignment"]
    assert spot["prompt"]["exercise_id"] == "spot.a7c2"
    return store, spot


def test_registered_paths_and_preview_gate(monkeypatch):
    assert {route.path for route in lesson.router.routes} >= {
        "/school/lessons/spot-cycle/", "/school/lessons/spot-cycle/transport",
        "/school/lessons/spot-cycle/saved-answer"}
    app = FastAPI()
    register_local_spot_preview(app)
    page = next(route.endpoint for route in app.routes
                if route.path == "/local/spot-cycle/session/{asset:path}")
    monkeypatch.setenv("BRIDGE_LOCAL_SPOT_PREVIEW", "1")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    assert page(request(), "learner-presentation.json").path.name == "learner-presentation.json"
    with pytest.raises(HTTPException) as denied:
        page(request(host="192.0.2.1"), "learner-presentation.json")
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as denied:
        page(request(), "teacher-versions.json")
    assert denied.value.status_code == 404


def test_school_admission_fails_closed_for_missing_or_wrong_binding(monkeypatch, tmp_path):
    store, _ = spot_store(tmp_path)
    with pytest.raises(HTTPException) as missing:
        lesson.require_learner_lesson_binding(request())
    assert missing.value.status_code == 503

    verified = lesson.VerifiedSchoolSession("school-1", "learner-1", "csrf", "http://testserver")
    binding = lesson.LearnerLessonBinding("school-1", "learner-1", "route-session", store.path)
    monkeypatch.setattr(lesson, "resolve_verified_lesson_binding", lambda _request: (verified, binding))
    assert lesson.require_learner_lesson_binding(request()) == binding
    with pytest.raises(HTTPException) as spoofed:
        lesson.require_learner_lesson_binding(request(headers=(("x-learner-id", "other"),)))
    assert spoofed.value.status_code == 403
    monkeypatch.setattr(lesson, "resolve_verified_lesson_binding", lambda _request: (verified,
                        lesson.LearnerLessonBinding("school-1", "learner-2", "route-session", store.path)))
    with pytest.raises(HTTPException) as foreign:
        lesson.require_learner_lesson_binding(request())
    assert foreign.value.status_code == 403
    monkeypatch.setattr(lesson, "resolve_verified_lesson_binding", lambda _request: (verified,
                        lesson.LearnerLessonBinding("school-1", "learner-1", "wrong-session", store.path)))
    with pytest.raises(HTTPException) as mismatched_journal:
        lesson.require_learner_lesson_binding(request())
    assert mismatched_journal.value.status_code == 403


def test_saved_answer_route_requires_terminal_ack(tmp_path):
    store, spot = spot_store(tmp_path)
    binding = lesson.LearnerLessonBinding("school-1", "learner-1", "route-session", store.path)
    with pytest.raises(HTTPException) as before:
        lesson.school_spot_saved_answer(binding)
    assert before.value.status_code == 409
    response = {"schema": "spot-first-play-response-v2", "exercise_id": "spot.a7c2",
                "prompt_sha256": spot["prompt"]["prompt_sha256"], "first_play": "DA",
                "guaranteed_spot_tricks_from_position": 1,
                "explanation": "My saved synthetic explanation."}
    ack(store, submit(store, "RESPONSE", spot["issuance_id"], response))
    terminal = submit(store, "ADVANCE", spot["issuance_id"])
    with pytest.raises(HTTPException) as before_ack:
        lesson.school_spot_saved_answer(binding)
    assert before_ack.value.status_code == 409
    ack(store, terminal)
    result = lesson.school_spot_saved_answer(binding)
    assert result.headers["cache-control"] == "private, no-store, max-age=0"
    assert result.body.decode().find("My saved synthetic explanation.") >= 0
    assert b'"next_assignment":null' in result.body
    assert b'"expected_solution"' not in result.body
    assert b'"journal_path"' not in result.body
