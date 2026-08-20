from bridge_school_api.ai import router as ai_router
from bridge_school_api.ai_decision import router as ai_decision_router
from bridge_school_api.ai_teacher import router as ai_teacher_router
from bridge_school_api.ai_worker import router as ai_worker_router


def test_ai_routes_are_registered():
    routers = [ai_router, ai_teacher_router, ai_worker_router, ai_decision_router]
    paths = {route.path for router in routers for route in router.routes}
    assert "/v1/ai/overview" in paths
    assert "/v1/ai/positions" in paths
    assert "/v1/ai/positions/{position_id}" in paths
    assert "/v1/ai/rules" in paths
    assert "/v1/ai/work-queue" in paths
    assert "/v1/ai/positions/{position_id}/finalize" in paths
