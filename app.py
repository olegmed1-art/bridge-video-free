"""Vercel entry point for the Bridge School FastAPI service."""

from fastapi import Depends

from bridge_school_api.ai import router as ai_router
from bridge_school_api.ai_decision import router as ai_decision_router
from bridge_school_api.ai_teacher import router as ai_teacher_router
from bridge_school_api.ai_worker import router as ai_worker_router
from bridge_school_api.main import app, require_api_token

app.include_router(ai_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_teacher_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_worker_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_decision_router, dependencies=[Depends(require_api_token)])

__all__ = ["app"]
