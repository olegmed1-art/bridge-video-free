"""Vercel entry point for the Bridge School FastAPI service."""

import base64
import json
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


def _safe_oidc_claims(token: str) -> dict[str, object]:
    """Decode only non-secret routing claims for temporary production diagnostics."""
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        return {
            key: claims.get(key)
            for key in ("iss", "aud", "sub", "owner_id", "project_id", "environment")
        }
    except Exception:
        return {"parse": "failed"}


@app.middleware("http")
async def vercel_oidc_context(request: Request, call_next):
    env_token = os.getenv("VERCEL_OIDC_TOKEN", "").strip()
    incoming_token = request.headers.get("x-vercel-oidc-token", "").strip()
    if request.url.path == "/dds3/readyz":
        diagnostic = {
            "env_present": bool(env_token),
            "incoming_present": bool(incoming_token),
            "env_claims": _safe_oidc_claims(env_token) if env_token else None,
            "incoming_claims": _safe_oidc_claims(incoming_token) if incoming_token else None,
        }
        print("VERCEL_OIDC_DIAG_V2 " + json.dumps(diagnostic, sort_keys=True), flush=True)
    if env_token:
        request.scope["headers"] = _replace_vercel_oidc_header(list(request.scope.get("headers", [])), env_token)
    return await call_next(request)


app.include_router(assistant_lab_bootstrap_router)
app.include_router(ai_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_teacher_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_policy_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_orchestrator_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_worker_router, dependencies=[Depends(require_api_token)])
app.include_router(ai_decision_router, dependencies=[Depends(require_api_token)])

__all__ = ["app"]
