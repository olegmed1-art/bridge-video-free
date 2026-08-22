"""Vercel entry point for the Bridge School FastAPI service."""

import os

from fastapi import Depends, Request

from bridge_school_api.ai import router as ai_router
from bridge_school_api.ai_decision import router as ai_decision_router
from bridge_school_api.ai_orchestrator import router as ai_orchestrator_router
from bridge_school_api.ai_policy import router as ai_policy_router
from bridge_school_api.ai_teacher import router as ai_teacher_router
from bridge_school_api.ai_worker import router as ai_worker_router
from bridge_school_api.assistant_lab_bootstrap import router as assistant_lab_bootstrap_router
from bridge_school_api.main import app, require_api_token


def _replace_vercel_oidc_header(headers: list[tuple[bytes, bytes]], token: str) -> list[tuple[bytes, bytes]]:
    """Trust only Vercel's deployment-scoped OIDC token, never a client-supplied copy."""
    clean = [(key, value) for key, value in headers if key.lower() != b"x-vercel-oidc-token"]
    if token:
        clean.append((b"x-vercel-oidc-token", token.encode("ascii")))
    return clean


@app.middleware("http")
async def vercel_oidc_context(request: Request, call_next):
    token = os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    if token:
        request.scope["headers"] = _replace_vercel_oidc_header(list(request.scope.get("headers", [])), token)
    return await call_next(request)


app.include_router(assistant_lab_bootstrap_router)
app.include_router(ai_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_teacher_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_policy_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_orchestrator_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_worker_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_decision_router, dependencies=[Depends(require_api_token)])

__all__ = ["app"]
