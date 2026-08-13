from __future__ import annotations

import logging
import os
import secrets
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from .db import EXPECTED_PRINCIPAL, connect

EXPECTED_SCHOOL = "Школа спортивного бриджа"
logger = logging.getLogger("bridge_school_api")

app = FastAPI(
    title="Bridge School API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    configured = os.environ.get("BRIDGE_API_TOKEN", "")
    if not configured:
        raise HTTPException(status_code=503, detail="application API token is not configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="missing bearer token")
    supplied = authorization[len(prefix):]
    if not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=403, detail="invalid bearer token")


def _database_failure_category(exc: Exception) -> str:
    text = str(exc).lower()
    if "password authentication failed" in text or "authentication failed" in text:
        return "authentication_failed"
    if "role" in text and "does not exist" in text:
        return "role_missing"
    if "database" in text and "does not exist" in text:
        return "database_missing"
    if "endpoint id is not specified" in text:
        return "endpoint_id_missing"
    if "could not translate host name" in text or "name or service not known" in text:
        return "dns_failed"
    if "network is unreachable" in text:
        return "network_unreachable"
    if "no route to host" in text:
        return "no_route_to_host"
    if "timeout" in text:
        return "connection_timeout"
    if "connection refused" in text:
        return "connection_refused"
    if "server closed the connection unexpectedly" in text:
        return "server_closed_connection"
    if "active endpoints limit exceeded" in text or "concurrently active endpoints" in text:
        return "active_endpoint_limit"
    if "remaining connection slots" in text:
        return "connection_limit"
    if "channel binding" in text:
        return "channel_binding_failed"
    if "ssl" in text or "certificate" in text:
        return "tls_failed"
    if isinstance(exc, psycopg.OperationalError):
        return "operational_error"
    return "database_error"


@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT current_user AS principal, count(*) AS school_count "
                "FROM public.school WHERE stable_name = %s GROUP BY current_user",
                (EXPECTED_SCHOOL,),
            )
            row = cur.fetchone()
    except Exception as exc:
        category = _database_failure_category(exc)
        logger.error(
            "database_health_check_failed category=%s type=%s sqlstate=%s",
            category,
            type(exc).__name__,
            getattr(exc, "sqlstate", None),
        )
        raise HTTPException(status_code=503, detail="service unavailable") from exc

    if not row or row["principal"] != EXPECTED_PRINCIPAL or row["school_count"] != 1:
        logger.error("database_health_check_failed category=runtime_boundary")
        raise HTTPException(status_code=503, detail="service unavailable")
    return JSONResponse(
        {"status": "ok"},
        headers={
            "Cache-Control": "public, max-age=0, must-revalidate",
            "Vercel-CDN-Cache-Control": "public, max-age=15, stale-while-revalidate=15",
        },
    )


@app.get("/v1/overview", dependencies=[Depends(require_api_token)])
def overview() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                s.school_id,
                s.stable_name,
                s.status,
                (SELECT count(*) FROM public.student st WHERE st.school_id=s.school_id) AS students,
                (SELECT count(*) FROM public.learning_group g WHERE g.school_id=s.school_id) AS groups,
                (SELECT count(*) FROM public.learning_interaction li WHERE li.school_id=s.school_id) AS interactions,
                (SELECT count(*) FROM public.media_asset ma WHERE ma.school_id=s.school_id) AS media_assets,
                (SELECT count(*) FROM public.transcript t WHERE t.school_id=s.school_id) AS transcripts,
                (SELECT count(*) FROM public.analysis_run ar WHERE ar.school_id=s.school_id) AS analysis_runs,
                (SELECT count(*) FROM public.artifact a WHERE a.school_id=s.school_id) AS artifacts
            FROM public.school s
            WHERE s.stable_name = %s
            """,
            (EXPECTED_SCHOOL,),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="school not found")
    return row


@app.get("/v1/students", dependencies=[Depends(require_api_token)])
def students(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                st.student_id,
                p.person_id,
                p.preferred_name,
                p.locale,
                p.timezone,
                st.current_status,
                st.school_joined_at,
                st.created_at
            FROM public.student st
            JOIN public.person p ON p.person_id = st.person_id
            JOIN public.school s ON s.school_id = st.school_id
            WHERE s.stable_name = %s
            ORDER BY p.preferred_name NULLS LAST, st.created_at
            LIMIT %s OFFSET %s
            """,
            (EXPECTED_SCHOOL, limit, offset),
        )
        return cur.fetchall()


@app.get("/v1/media", dependencies=[Depends(require_api_token)])
def media(limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ma.media_asset_id,
                ma.duration_seconds,
                ma.status,
                ma.created_at,
                t.transcript_id,
                t.transcript_type,
                t.language,
                t.status AS transcript_status,
                count(ts.transcript_segment_id) AS segment_count
            FROM public.media_asset ma
            JOIN public.school s ON s.school_id = ma.school_id
            LEFT JOIN public.transcript t ON t.media_asset_id = ma.media_asset_id
            LEFT JOIN public.transcript_segment ts ON ts.transcript_id = t.transcript_id
            WHERE s.stable_name = %s
            GROUP BY ma.media_asset_id, t.transcript_id
            ORDER BY ma.created_at DESC, t.created_at DESC NULLS LAST
            LIMIT %s OFFSET %s
            """,
            (EXPECTED_SCHOOL, limit, offset),
        )
        return cur.fetchall()


@app.get("/v1/transcripts/{transcript_id}/segments", dependencies=[Depends(require_api_token)])
def transcript_segments(
    transcript_id: UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                ts.transcript_segment_id,
                ts.sequence_no,
                ts.start_seconds,
                ts.end_seconds,
                ts.speaker_label,
                ts.text,
                ts.confidence_class,
                ts.confidence_value
            FROM public.transcript_segment ts
            JOIN public.transcript t ON t.transcript_id = ts.transcript_id
            JOIN public.school s ON s.school_id = t.school_id
            WHERE ts.transcript_id = %s AND s.stable_name = %s
            ORDER BY ts.sequence_no
            LIMIT %s OFFSET %s
            """,
            (transcript_id, EXPECTED_SCHOOL, limit, offset),
        )
        return cur.fetchall()
