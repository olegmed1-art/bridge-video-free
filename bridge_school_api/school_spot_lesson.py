"""Closed school-route foundation for the existing local teacher session.

There is no learner-authenticated HTTP principal in this repository. Until the
school host supplies one, every route fails before serving an asset or journal.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
import json
import os
from pathlib import Path
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from .local_spot_preview import SESSION_ALLOWED, SESSION_ASSETS
from .local_spot_session import (
    LocalSpotSessionUnavailable,
    apply_operation_to_store,
    load_store_class,
)
from .spot_review_answer import project_saved_review


@dataclass(frozen=True)
class LearnerLessonBinding:
    """Server-resolved school principal and its pre-existing local journal."""

    school_id: str
    learner_id: str
    session_id: str
    journal_path: Path


@dataclass(frozen=True)
class VerifiedSchoolSession:
    """Identity and CSRF values established by the school's existing verifier."""

    school_id: str
    learner_id: str
    csrf_token: str
    origin: str
    binding_kind: str = 'SCHOOL_VERIFIED'


def resolve_verified_lesson_binding(request: Request) -> tuple[VerifiedSchoolSession, LearnerLessonBinding]:
    """One request-scoped, explicitly installed resolver; no production fallback."""
    from .school_spot_repository import SchoolLessonOwnershipRejected
    app = request.scope.get('app')
    resolver = getattr(getattr(app, 'state', None), 'school_spot_binding_resolver', None)
    if not callable(resolver):
        raise HTTPException(status_code=503, detail='school lesson resolver unavailable')
    try:
        pair = resolver(request)
    except (SchoolLessonOwnershipRejected, OSError, ValueError) as exc:
        raise HTTPException(status_code=403, detail='lesson ownership rejected') from exc
    if (type(pair) is not tuple or len(pair) != 2 or
            not isinstance(pair[0], VerifiedSchoolSession) or
            not isinstance(pair[1], LearnerLessonBinding)):
        raise HTTPException(status_code=503, detail='school lesson resolver unavailable')
    if pair[0].binding_kind not in {'SCHOOL_VERIFIED', 'LOCAL_SINGLE_USER'}:
        raise HTTPException(status_code=503, detail='unknown binding kind')
    if pair[0].binding_kind == 'LOCAL_SINGLE_USER' and (
            getattr(app.state, 'school_spot_mode', None) != 'LOCAL_SINGLE_USER'
            or os.environ.get('VERCEL_ENV')):
        raise HTTPException(status_code=503, detail='local context unavailable')
    return pair


def require_learner_lesson_binding(request: Request) -> LearnerLessonBinding:
    """Admit only a verified learner bound to the matching existing journal.

    The installed host resolver owns verification and journal lookup. Browser headers are
    never used to select the principal, session, or journal. A shared
    BRIDGE_API_TOKEN is not a learner identity.
    """

    verified, binding = resolve_verified_lesson_binding(request)
    if not isinstance(verified, VerifiedSchoolSession):
        raise HTTPException(status_code=503, detail="school browser session unavailable")
    if not isinstance(binding, LearnerLessonBinding):
        raise HTTPException(status_code=503, detail="school lesson binding unavailable")
    if (
        not verified.school_id
        or not verified.learner_id
        or not verified.csrf_token
        or not verified.origin
        or binding.school_id != verified.school_id
        or binding.learner_id != verified.learner_id
        or request.headers.get("x-learner-id") is not None
        or request.headers.get("x-school-id") is not None
    ):
        raise HTTPException(status_code=403, detail="lesson ownership rejected")
    if request.method not in {"GET", "HEAD"}:
        origin = request.headers.get("origin", "")
        csrf = request.headers.get("x-csrf-token", "")
        try:
            valid = secrets.compare_digest(origin, verified.origin) and secrets.compare_digest(
                csrf, verified.csrf_token
            )
        except TypeError:
            valid = False
        if not valid:
            raise HTTPException(status_code=403, detail="lesson CSRF rejected")
    try:
        store_for_binding(binding).resume()
    except (LocalSpotSessionUnavailable, ValueError, OSError) as exc:
        raise HTTPException(status_code=403, detail="lesson journal binding rejected") from exc
    request.state.school_lesson_csrf_token = verified.csrf_token
    request.state.school_lesson_binding_kind = verified.binding_kind
    return binding


def store_for_binding(binding: LearnerLessonBinding):
    if (
        not binding.school_id
        or not binding.learner_id
        or not binding.session_id
        or not isinstance(binding.journal_path, Path)
        or not binding.journal_path.is_file()
    ):
        raise LocalSpotSessionUnavailable("BOUND_SCHOOL_SESSION_UNAVAILABLE")
    return load_store_class()(binding.journal_path, binding.session_id)


router = APIRouter(prefix="/school/lessons/spot-cycle", include_in_schema=False)


@router.post("/transport")
async def school_spot_transport(
    request: Request,
    binding: LearnerLessonBinding = Depends(require_learner_lesson_binding),
) -> JSONResponse:
    body = await request.body()
    if len(body) > 32768:
        raise HTTPException(status_code=413)
    try:
        message = json.loads(body)
        if (
            type(message) is not dict
            or set(message) != {"operation", "value"}
            or type(message["value"]) is not dict
        ):
            raise ValueError("INVALID_LESSON_MESSAGE")
        result = apply_operation_to_store(
            store_for_binding(binding), message["operation"], message["value"]
        )
    except LocalSpotSessionUnavailable as exc:
        raise HTTPException(status_code=503, detail="lesson session unavailable") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=409, detail="lesson command rejected") from exc
    return JSONResponse(result, headers={"Cache-Control": "private, no-store, max-age=0"})


@router.get("/saved-answer")
def school_spot_saved_answer(
    binding: LearnerLessonBinding = Depends(require_learner_lesson_binding),
) -> JSONResponse:
    try:
        result = project_saved_review(store_for_binding(binding))
    except (LocalSpotSessionUnavailable, ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail="saved answer unavailable") from exc
    return JSONResponse(result, headers={"Cache-Control": "private, no-store, max-age=0"})


@router.get("/")
@router.get("/{asset:path}")
def school_spot_page(
    request: Request,
    asset: str = "",
    binding: LearnerLessonBinding = Depends(require_learner_lesson_binding),
) -> Response:
    try:
        store_for_binding(binding)
    except LocalSpotSessionUnavailable as exc:
        raise HTTPException(status_code=503, detail="lesson session unavailable") from exc
    filename = asset or "index.html"
    if filename not in SESSION_ALLOWED:
        raise HTTPException(status_code=404)
    headers = {
        "Cache-Control": "private, no-store, max-age=0",
        "X-Robots-Tag": "noindex, nofollow",
        "Content-Security-Policy": "default-src 'self'; base-uri 'none'; frame-ancestors 'none'",
    }
    if filename == "index.html":
        source = (SESSION_ASSETS / filename).read_text(encoding="utf-8")
        token = escape(request.state.school_lesson_csrf_token, quote=True)
        page = source.replace("<head>", f'<head>\n  <meta name="bridge-school-csrf-token" content="{token}">', 1)
        if request.state.school_lesson_binding_kind == 'LOCAL_SINGLE_USER':
            ids = ','.join(request.state.school_spot_assigned_ids)
            page = page.replace('<head>', '<head>\n  <meta name="bridge-local-assigned-activities" '
                                f'content="{escape(ids, quote=True)}">', 1)
            if getattr(request.app.state, 'diana19_service', None) is not None:
                page = page.replace('<head>', '<head>\n<meta name="bridge-diana19-staging" content="enabled">', 1)
        return HTMLResponse(page, headers=headers)
    return FileResponse(SESSION_ASSETS / filename, media_type=SESSION_ALLOWED[filename], headers=headers)
