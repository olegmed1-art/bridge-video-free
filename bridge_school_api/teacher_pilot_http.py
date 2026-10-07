"""Inert router factory: exact preview gate + injected existing API authorization."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
from typing import Mapping

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .teacher_pilot_sessions import PilotError

logger = logging.getLogger(__name__)
MAX_BODY = 1024


@dataclass(frozen=True)
class PreviewGate:
    approved_branch: str
    approved_sha: str
    deployment_host: str
    environment: Mapping[str, str]

    def check(self, request: Request):
        env = self.environment
        enabled = (env.get("BRIDGE_TEACHER_PILOT_ENABLED") == "1"
                   and env.get("VERCEL_ENV") == "preview"
                   and env.get("VERCEL_GIT_COMMIT_REF") == self.approved_branch
                   and env.get("VERCEL_GIT_COMMIT_SHA") == self.approved_sha
                   and re.fullmatch(r"[0-9a-f]{40}", self.approved_sha) is not None
                   and self.approved_branch.startswith("test/teacher-")
                   and re.fullmatch(r"[a-z0-9-]+\.vercel\.app", self.deployment_host) is not None
                   and env.get("VERCEL_URL") == self.deployment_host)
        if not enabled:
            raise HTTPException(404, "TEACHER_PILOT_DISABLED")
        hosts = request.headers.getlist("host")
        if hosts != [self.deployment_host]:
            raise HTTPException(403, "HOST_REJECTED")
        origins = request.headers.getlist("origin")
        if request.method == "POST" and origins != ["https://" + self.deployment_host]:
            raise HTTPException(403, "ORIGIN_REJECTED")
        if request.method == "GET" and origins not in ([], ["https://" + self.deployment_host]):
            raise HTTPException(403, "ORIGIN_REJECTED")


def parse_body(raw):
    if len(raw) > MAX_BODY:
        raise HTTPException(413, "BODY_LIMIT")

    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("duplicate")
            out[key] = value
        return out

    def nonfinite(_):
        raise ValueError("nonfinite")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                           parse_constant=nonfinite)
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(422, "JSON_INVALID") from None
    if type(value) is not dict:
        raise HTTPException(422, "JSON_OBJECT_REQUIRED")
    return value


async def body(request):
    if request.headers.getlist("content-type") != ["application/json"]:
        raise HTTPException(415, "JSON_REQUIRED")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise HTTPException(413, "BODY_LIMIT")
    return parse_body(bytes(data))


def create_router(*, gate: PreviewGate, authorize, service_factory):
    """Inject require_api_token. Never embed its value in HTML, JS, manifest or cookie.

    Only protected API endpoints are provided here. The browser authentication
    boundary and private content installation require a separate reviewed preview.
    service_factory must lazily create the pinned runtime and PostgresStore.
    """
    router = APIRouter(prefix="/v1/teacher-pilot",
                       dependencies=[Depends(gate.check), Depends(authorize)])

    async def invoke(method, *args):
        try:
            return await run_in_threadpool(lambda: getattr(service_factory(), method)(*args))
        except PilotError as exc:
            if exc.code == "COMMIT_OUTCOME_UNKNOWN":
                detail = {"code": exc.code}
                recovery = getattr(exc, "recovery_session_id", None)
                if isinstance(recovery, str):
                    detail["session_id"] = recovery
                raise HTTPException(503, detail) from None
            conflicts = {"STALE_TURN", "SESSION_CONTENT_MISMATCH", "CHECKPOINT_IDENTITY",
                         "RUNTIME_TRANSITION", "EVENT_LIMIT"}
            status = (503 if exc.code == "COMMIT_OUTCOME_UNKNOWN" else
                      404 if exc.code == "SESSION_NOT_FOUND" else 409 if exc.code in conflicts else 422)
            raise HTTPException(status, exc.code) from None
        except ValueError as exc:
            # Domain errors expose their fixed symbolic code; never arbitrary source text.
            code = getattr(exc, "code", None)
            if isinstance(code, str) and re.fullmatch("[A-Z][A-Z0-9_]{0,63}", code):
                raise HTTPException(422, code) from None
            logger.error("teacher_pilot_failed phase=runtime")
            raise HTTPException(503, "TEACHER_PILOT_UNAVAILABLE") from None
        except Exception:
            logger.error("teacher_pilot_failed phase=storage")
            raise HTTPException(503, "TEACHER_PILOT_UNAVAILABLE") from None

    def respond(value):
        return JSONResponse(value, headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        })

    @router.post("/sessions")
    async def start(request: Request):
        payload = await body(request)
        if payload != {}:
            raise HTTPException(422, "CREATE_FIELDS")
        return respond(await invoke("start"))

    @router.get("/sessions/{session_id}")
    async def read(session_id: str):
        return respond(await invoke("read", session_id))

    @router.post("/sessions/{session_id}/events")
    async def event(session_id: str, request: Request):
        return respond(await invoke("event", session_id, await body(request)))

    return router
