"""The school URL is closed until a principal-bound authorization is supplied."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import bridge_school_api.school_spot_lesson as school_lesson
from bridge_school_api.local_spot_session import load_store_class
from bridge_school_api.school_spot_lesson import (
    LearnerLessonBinding,
    VerifiedSchoolSession,
    router,
)


URL = "/school/lessons/spot-cycle/"
SOURCE_ROOT = Path(__file__).resolve().parents[1]


def test_school_route_rejects_api_token_and_spoofed_identity(monkeypatch):
    from app import app

    monkeypatch.setenv("BRIDGE_API_TOKEN", "test-shared-api-token")
    monkeypatch.setenv("BRIDGE_LOCAL_SPOT_PREVIEW", "1")
    client = TestClient(app, client=("127.0.0.1", 50000))
    headers = {
        "Authorization": "Bearer test-shared-api-token",
        "X-Learner-Id": "someone-else",
        "X-School-Id": "some-school",
    }
    assert client.get(URL, headers=headers).status_code == 503
    assert client.get(URL + "lesson-ui.js", headers=headers).status_code == 503
    assert client.get(URL + "teacher-review.js", headers=headers).status_code == 503
    assert client.get(URL + "learner-presentation.json", headers=headers).status_code == 503
    assert client.get(URL + "saved-answer", headers=headers).status_code == 503
    assert client.post(URL + "transport", json={"operation": "resume", "value": {}}, headers=headers).status_code == 503


def test_school_route_uses_only_server_bound_existing_store(monkeypatch, tmp_path):
    assert (SOURCE_ROOT / "evolutionary_course" / "teacher_session_store.py").is_file()
    journal = tmp_path / "school-lesson.sqlite3"
    load_store_class().create(journal, "owned-session", preview_exercise_id="spot.a7c2")
    binding = LearnerLessonBinding("school-1", "learner-1", "owned-session", journal)
    verified = VerifiedSchoolSession("school-1", "learner-1", "test-csrf", "http://testserver")
    monkeypatch.setattr(school_lesson, "verify_school_browser_session", lambda request: verified)
    monkeypatch.setattr(school_lesson, "lookup_existing_lesson_session", lambda principal: binding)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app, client=("127.0.0.1", 50000))
    write_headers = {"Origin": "http://testserver", "X-CSRF-Token": "test-csrf"}

    page = client.get(URL)
    assert page.status_code == 200
    assert 'name="bridge-school-csrf-token" content="test-csrf"' in page.text
    assert client.get(URL + "session-host.js").status_code == 200
    assert client.get(URL + "learner-presentation.json").status_code == 200
    assert client.get(URL + "saved-answer").status_code == 409
    assert client.get(URL + "not-an-asset").status_code == 404
    response = client.post(URL + "transport", json={"operation": "resume", "value": {}}, headers=write_headers)
    assert response.status_code == 200
    assert response.json()["session_id"] == "owned-session"
    assert response.json()["current_assignment"]["prompt"]["exercise_id"] == "entry.e1"
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert client.post(URL + "transport", json={"operation": "reset", "value": {}}, headers=write_headers).status_code == 409

    assert client.post(URL + "transport", json={"operation": "resume", "value": {}}).status_code == 403
    assert client.post(URL + "transport", json={"operation": "resume", "value": {}},
                       headers={"Origin": "http://evil.example", "X-CSRF-Token": "test-csrf"}).status_code == 403
    assert client.post(URL + "transport", json={"operation": "resume", "value": {}},
                       headers={"Origin": "http://testserver", "X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get(URL, headers={"X-Learner-Id": "learner-2"}).status_code == 403
    assert client.get(URL, headers={"X-School-Id": "school-2"}).status_code == 403

    monkeypatch.setattr(school_lesson, "lookup_existing_lesson_session", lambda principal:
                        LearnerLessonBinding("school-2", "learner-1", "owned-session", journal))
    assert client.get(URL).status_code == 403
    monkeypatch.setattr(school_lesson, "lookup_existing_lesson_session", lambda principal:
                        LearnerLessonBinding("school-1", "learner-2", "owned-session", journal))
    assert client.get(URL).status_code == 403

    foreign = tmp_path / "foreign.sqlite3"
    load_store_class().create(foreign, "foreign-session", preview_exercise_id="spot.a7c2")
    monkeypatch.setattr(school_lesson, "lookup_existing_lesson_session", lambda principal:
                        LearnerLessonBinding("school-1", "learner-1", "owned-session", foreign))
    assert client.get(URL).status_code == 403
