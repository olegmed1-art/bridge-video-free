"""Loopback-only local preview of the compact spot-trick lesson.

The route is intentionally absent from the public API contract and disabled in
Vercel even if an operator accidentally sets the local opt-in flag there.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .local_spot_session import LocalSpotSessionUnavailable, apply_operation
from .local_two_spot_feedback import feedback_for_answer


ASSETS = Path(__file__).resolve().parent / "static" / "spot_cycle"
ALLOWED = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "two-spot.html": "text/html; charset=utf-8",
    "two-spot.js": "text/javascript; charset=utf-8",
    "two-spot.css": "text/css; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "compact.css": "text/css; charset=utf-8",
}
SESSION_ASSETS = Path(__file__).resolve().parent / "static" / "spot_cycle_session"
SESSION_ALLOWED = {
    "index.html": "text/html; charset=utf-8",
    "learner-presentation.json": "application/json; charset=utf-8",
    "spot-learner.js": "text/javascript; charset=utf-8",
    "teacher-client.js": "text/javascript; charset=utf-8",
    "teacher-review.js": "text/javascript; charset=utf-8",
    "store-transport.js": "text/javascript; charset=utf-8",
    "lesson-ui.js": "text/javascript; charset=utf-8",
    "session-host.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "figma.css": "text/css; charset=utf-8",
    "transport.css": "text/css; charset=utf-8",
}


def preview_allowed(environ: dict[str, str], client_host: str | None) -> bool:
    return (
        environ.get("BRIDGE_LOCAL_SPOT_PREVIEW") == "1"
        and not environ.get("VERCEL_ENV")
        and client_host in {"127.0.0.1", "::1"}
    )


def register_local_spot_preview(app: FastAPI) -> None:
    @app.post("/local/spot-cycle/feedback", include_in_schema=False)
    async def local_two_spot_feedback(request: Request) -> JSONResponse:
        if not preview_allowed(os.environ, request.client.host if request.client else None):
            raise HTTPException(status_code=404)
        body = await request.body()
        if len(body) > 16384:
            raise HTTPException(status_code=413)
        try:
            import json
            result = feedback_for_answer(json.loads(body))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.post("/local/spot-cycle/session/transport", include_in_schema=False)
    async def local_spot_transport(request: Request) -> JSONResponse:
        if not preview_allowed(os.environ, request.client.host if request.client else None):
            raise HTTPException(status_code=404)
        body = await request.body()
        if len(body) > 32768:
            raise HTTPException(status_code=413)
        try:
            import json
            message = json.loads(body)
            if type(message) is not dict or set(message) != {"operation", "value"} or type(message["value"]) is not dict:
                raise ValueError("INVALID_LOCAL_SESSION_MESSAGE")
            result = apply_operation(message["operation"], message["value"])
        except LocalSpotSessionUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/local/spot-cycle/session/saved-answer", include_in_schema=False)
    def local_spot_saved_answer(request: Request) -> JSONResponse:
        if not preview_allowed(os.environ, request.client.host if request.client else None):
            raise HTTPException(status_code=404)
        from .local_spot_session import bound_store
        from .spot_review_answer import project_saved_review
        try:
            result = project_saved_review(bound_store())
        except (LocalSpotSessionUnavailable, ValueError, OSError) as exc:
            raise HTTPException(status_code=409, detail="saved answer unavailable") from exc
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    @app.get("/local/spot-cycle/session/", include_in_schema=False)
    @app.get("/local/spot-cycle/session/{asset:path}", include_in_schema=False)
    def local_spot_session_page(request: Request, asset: str = "") -> FileResponse:
        if not preview_allowed(os.environ, request.client.host if request.client else None):
            raise HTTPException(status_code=404)
        filename = asset or "index.html"
        if filename not in SESSION_ALLOWED:
            raise HTTPException(status_code=404)
        return FileResponse(
            SESSION_ASSETS / filename,
            media_type=SESSION_ALLOWED[filename],
            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
        )

    @app.get("/local/spot-cycle/", include_in_schema=False)
    @app.get("/local/spot-cycle/{asset:path}", include_in_schema=False)
    def local_spot_preview(request: Request, asset: str = "") -> FileResponse:
        if not preview_allowed(os.environ, request.client.host if request.client else None):
            raise HTTPException(status_code=404)
        filename = asset or "two-spot.html"
        if filename not in ALLOWED:
            raise HTTPException(status_code=404)
        return FileResponse(
            ASSETS / filename,
            media_type=ALLOWED[filename],
            headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow"},
        )
