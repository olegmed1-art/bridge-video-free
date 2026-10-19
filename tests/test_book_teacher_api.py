from fastapi.testclient import TestClient
import pytest

import bridge_school_api.knowledge as knowledge
from bridge_school_api.main import app
from test_book_world import malformed_teacher_items, stored_fixture
from test_book_world_review_binding import drifted_teacher_items
from test_knowledge_read_api import FakeCursor, install_fake_connect


def test_book_teacher_uses_protected_existing_world_query(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-test-token")
    item = stored_fixture()
    cursor = FakeCursor(rows=[item])
    install_fake_connect(monkeypatch, cursor)
    client = TestClient(app)
    path = "/v1/knowledge/teacher/book"
    params = {"stable_key": item["stable_key"]}
    assert client.get(path, params=params).status_code == 401
    assert not cursor.executions
    assert client.get(path, params=params, headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert not cursor.executions
    response = client.get(path, params=params, headers={"Authorization": "Bearer synthetic-test-token"})
    assert response.status_code == 200
    assert response.json()["knowledge_version_id"] == item["item_id"]
    assert response.json()["citations"][0] == item["content"]["citation"]
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    sql, parameters = cursor.executions[0]
    assert "FROM public.knowledge_item" in sql
    assert parameters[1:4] == ("external", "SYSTEM_NEUTRAL", item["stable_key"])


def test_book_teacher_does_not_fallback_on_missing_ambiguous_or_invalid_rows(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-test-token")
    client = TestClient(app)
    item = stored_fixture()
    for rows, status in [([], 404), ([item, item], 409), ([{**item, "review_status": "unreviewed"}], 409)]:
        install_fake_connect(monkeypatch, FakeCursor(rows=rows))
        response = client.get("/v1/knowledge/teacher/book", params={"stable_key": item["stable_key"]},
                              headers={"Authorization": "Bearer synthetic-test-token"})
        assert response.status_code == status


@pytest.mark.parametrize("label,item", list(malformed_teacher_items()), ids=lambda value: value if isinstance(value, str) else None)
def test_book_teacher_malformed_evidence_is_409(monkeypatch, label, item):
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-test-token")
    cursor = FakeCursor(rows=[item])
    install_fake_connect(monkeypatch, cursor)
    response = TestClient(app).get("/v1/knowledge/teacher/book",
        params={"stable_key": item["stable_key"]},
        headers={"Authorization": "Bearer synthetic-test-token"})
    assert response.status_code == 409
    assert response.json() == {"detail": "BOOK_WORLD_EVIDENCE_INVALID"}
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert len(cursor.executions) == 1


@pytest.mark.parametrize("label,item", list(drifted_teacher_items()),
                         ids=[label for label, _ in drifted_teacher_items()])
def test_book_teacher_rehashed_review_drift_is_409(monkeypatch, label, item):
    monkeypatch.setenv("BRIDGE_API_TOKEN", "synthetic-test-token")
    cursor = FakeCursor(rows=[item])
    install_fake_connect(monkeypatch, cursor)
    response = TestClient(app).get("/v1/knowledge/teacher/book",
        params={"stable_key": item["stable_key"]},
        headers={"Authorization": "Bearer synthetic-test-token"})
    assert response.status_code == 409
    assert response.json() == {"detail": "BOOK_WORLD_EVIDENCE_INVALID"}
    assert response.headers["cache-control"] == "private, no-store, max-age=0"
    assert len(cursor.executions) == 1
