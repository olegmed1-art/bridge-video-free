"""Local FastAPI route contract; no external service or credentials required."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bridge_school_api.local_spot_preview import preview_allowed, register_local_spot_preview


def test_local_opt_in_only():
    assert preview_allowed({"BRIDGE_LOCAL_SPOT_PREVIEW": "1"}, "127.0.0.1")
    assert preview_allowed({"BRIDGE_LOCAL_SPOT_PREVIEW": "1"}, "::1")
    assert not preview_allowed({}, "127.0.0.1")
    assert not preview_allowed({"BRIDGE_LOCAL_SPOT_PREVIEW": "1", "VERCEL_ENV": "preview"}, "127.0.0.1")
    assert not preview_allowed({"BRIDGE_LOCAL_SPOT_PREVIEW": "1"}, "192.0.2.1")


def test_real_http_route_and_assets(monkeypatch):
    app = FastAPI()
    register_local_spot_preview(app)
    client = TestClient(app, client=("127.0.0.1", 50000))
    monkeypatch.setenv("BRIDGE_LOCAL_SPOT_PREVIEW", "1")
    monkeypatch.delenv("VERCEL_ENV", raising=False)

    page = client.get("/local/spot-cycle/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert "Две независимые позиции" in page.text
    assert page.headers["cache-control"] == "no-store"
    for asset in ("two-spot.js", "two-spot.css", "styles.css", "compact.css"):
        assert client.get(f"/local/spot-cycle/{asset}").status_code == 200
    assert client.get("/local/spot-cycle/teacher-key").status_code == 404

    monkeypatch.delenv("BRIDGE_LOCAL_SPOT_PREVIEW")
    assert client.get("/local/spot-cycle/").status_code == 404
    monkeypatch.setenv("BRIDGE_LOCAL_SPOT_PREVIEW", "1")
    monkeypatch.setenv("VERCEL_ENV", "preview")
    assert client.get("/local/spot-cycle/").status_code == 404


def test_actual_app_entrypoint_route(monkeypatch):
    from app import app as runtime_app

    monkeypatch.setenv("BRIDGE_LOCAL_SPOT_PREVIEW", "1")
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    loopback = TestClient(runtime_app, client=("127.0.0.1", 50000))
    response = loopback.get("/local/spot-cycle/")
    assert response.status_code == 200
    assert "Защитники скрыты" in response.text
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert TestClient(runtime_app, client=("192.0.2.1", 50000)).get("/local/spot-cycle/").status_code == 404
