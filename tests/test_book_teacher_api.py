from fastapi.testclient import TestClient

import bridge_school_api.knowledge as knowledge
from bridge_school_api.main import app
from test_book_world import stored_fixture
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
