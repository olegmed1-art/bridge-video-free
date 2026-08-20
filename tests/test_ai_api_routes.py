from app import app


def test_ai_routes_are_registered():
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v1/ai/overview" in paths
    assert "/v1/ai/positions" in paths
    assert "/v1/ai/positions/{position_id}" in paths
    assert "/v1/ai/rules" in paths
    assert "/v1/ai/work-queue" in paths
    assert "/v1/ai/positions/{position_id}/finalize" in paths
