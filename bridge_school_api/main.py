from __future__ import annotations

import logging
import os
import secrets
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.responses import Response

from assistant_lab.contract import CONTRACT_VERSION, LabContractError, validate_job_payload, verify_dds3_result
from assistant_lab.dispatch import verify_dispatch_nonce

from .db import DatabaseConfigurationError, EXPECTED_PRINCIPAL, connect
from .dds3 import DDSUnavailable, solve_table
from .dds3.readiness import engine_readiness
from .dds3.remote import RemoteDDS3Config, compute_remote, remote_engine_readiness

EXPECTED_SCHOOL = "Школа спортивного бриджа"
ASSISTANT_LAB_DISPATCHER = "vercel-capability-v1"
logger = logging.getLogger("bridge_school_api")

app = FastAPI(title="Bridge School API", version="0.2.0", docs_url=None, redoc_url=None, openapi_url=None)


def apply_response_security_headers(path: str, response: Response) -> Response:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if path.startswith("/v1/"):
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        for header_name in ("Vercel-CDN-Cache-Control", "CDN-Cache-Control"):
            if header_name in response.headers:
                del response.headers[header_name]
    return response


@app.middleware("http")
async def api_security_headers(request: Request, call_next):
    response = await call_next(request)
    return apply_response_security_headers(request.url.path, response)


def require_api_token(authorization: str | None = Header(default=None)) -> None:
    configured = os.environ.get("BRIDGE_API_TOKEN", "")
    if not configured:
        raise HTTPException(status_code=503, detail="application API token is not configured")
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="missing bearer token")
    if not secrets.compare_digest(authorization[len(prefix):], configured):
        raise HTTPException(status_code=403, detail="invalid bearer token")


class DDS3TableRequest(BaseModel):
    pbn: str = Field(min_length=1, max_length=512)
    dealer: str = Field(default="N", pattern="^[NESWnesw]$")
    vulnerability: str = Field(default="None", max_length=8)


def _remote_dds3_config() -> RemoteDDS3Config | None:
    url = os.getenv("DDS3_REMOTE_URL", "").strip().rstrip("/")
    if not url:
        return None
    return RemoteDDS3Config(
        base_url=url,
        timeout_seconds=float(os.getenv("DDS3_REMOTE_TIMEOUT_SECONDS", "25")),
    )


def _vercel_oidc_token(request: Request) -> str:
    return request.headers.get("x-vercel-oidc-token", "").strip()


@app.get("/dds3/readyz")
def dds3_readyz(request: Request) -> JSONResponse:
    remote = _remote_dds3_config()
    if remote is None:
        result = engine_readiness()
    else:
        result = remote_engine_readiness(
            bearer_token=_vercel_oidc_token(request),
            config=remote,
        )
    return JSONResponse(result, status_code=200 if result["status"] == "ready" else 503, headers={"Cache-Control": "no-store"})


@app.post("/v1/dds3/table", dependencies=[Depends(require_api_token)])
def dds3_table(request: DDS3TableRequest, http_request: Request) -> dict:
    try:
        remote = _remote_dds3_config()
        if remote is not None:
            return compute_remote(
                {
                    "operation": "dd_table",
                    "pbn": request.pbn,
                    "dealer": request.dealer,
                    "vulnerability": request.vulnerability,
                },
                bearer_token=_vercel_oidc_token(http_request),
                config=remote,
            )
        return solve_table(pbn=request.pbn, dealer=request.dealer, vulnerability=request.vulnerability)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DDSUnavailable as exc:
        logger.error("dds3_request_failed category=dds_unavailable")
        raise HTTPException(status_code=503, detail="DDS_UNAVAILABLE") from exc


def _assistant_lab_finish(
    job_id: UUID,
    *,
    status: str,
    result: dict | None = None,
    error_text: str | None = None,
    requeue: bool = False,
) -> None:
    provenance = {
        "assistant_lab_contract": CONTRACT_VERSION,
        "dispatcher": ASSISTANT_LAB_DISPATCHER,
        "execution_path": "vercel_oidc_to_oracle_dds3" if result and result.get("engine") == "DDS3" else "vercel_noop",
    }
    with connect() as conn, conn.cursor() as cur:
        if requeue:
            cur.execute(
                """
                UPDATE assistant_lab.job
                SET status='QUEUED', claimed_by=NULL, claimed_at=NULL, heartbeat_at=NULL,
                    error_text=%s, not_before=now() + interval '2 seconds',
                    provenance_json=provenance_json || %s::jsonb
                WHERE job_id=%s AND status='RUNNING' AND claimed_by=%s
                """,
                (error_text, psycopg.types.json.Jsonb(provenance), job_id, ASSISTANT_LAB_DISPATCHER),
            )
        else:
            cur.execute(
                """
                UPDATE assistant_lab.job
                SET status=%s, result_json=%s, error_text=%s, completed_at=now(),
                    heartbeat_at=now(), dispatch_nonce_sha256=NULL,
                    provenance_json=provenance_json || %s::jsonb
                WHERE job_id=%s AND status='RUNNING' AND claimed_by=%s
                """,
                (
                    status,
                    psycopg.types.json.Jsonb(result) if result is not None else None,
                    error_text,
                    psycopg.types.json.Jsonb(provenance),
                    job_id,
                    ASSISTANT_LAB_DISPATCHER,
                ),
            )
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail="assistant-lab dispatch ownership changed")
        conn.commit()


@app.get("/v1/assistant-lab/jobs/{job_id}/dispatch")
def dispatch_assistant_lab_job(
    job_id: UUID,
    request: Request,
    nonce: str = Query(min_length=48, max_length=128),
) -> JSONResponse:
    """Capability-dispatch exactly one pre-created bounded lab job.

    This GET is intentionally idempotent/bounded so the connected Vercel fetch tool
    can trigger an interactive computation without exposing the general application
    API token. Possessing the 256-bit nonce is the capability; the database stores
    only its SHA-256 digest. The endpoint cannot create jobs or accept job payloads.
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT job_id, kind, payload_json, status, result_json, error_text,
                   dispatch_nonce_sha256, deadline_at, attempts, max_attempts
            FROM assistant_lab.job
            WHERE job_id=%s
            FOR UPDATE
            """,
            (job_id,),
        )
        row = cur.fetchone()
        if not row or not verify_dispatch_nonce(row["dispatch_nonce_sha256"], nonce):
            raise HTTPException(status_code=404, detail="assistant-lab job not found")
        if row["status"] == "COMPLETED":
            return JSONResponse({"job_id": str(job_id), "status": "COMPLETED", "result": row["result_json"]})
        if row["status"] in {"FAILED", "CANCELLED"}:
            return JSONResponse(
                {"job_id": str(job_id), "status": row["status"], "detail": "assistant-lab job terminal"},
                status_code=409,
            )
        if row["status"] == "RUNNING":
            return JSONResponse({"job_id": str(job_id), "status": "RUNNING"}, status_code=202)
        if row["deadline_at"] is not None:
            cur.execute("SELECT now() >= %s AS expired", (row["deadline_at"],))
            if cur.fetchone()["expired"]:
                cur.execute(
                    """
                    UPDATE assistant_lab.job
                    SET status='CANCELLED', error_text='DISPATCH_DEADLINE_EXPIRED',
                        completed_at=now(), dispatch_nonce_sha256=NULL
                    WHERE job_id=%s AND status='QUEUED'
                    """,
                    (job_id,),
                )
                conn.commit()
                raise HTTPException(status_code=410, detail="assistant-lab dispatch expired")
        try:
            payload = validate_job_payload(row["kind"], row["payload_json"])
        except LabContractError as exc:
            cur.execute(
                """
                UPDATE assistant_lab.job
                SET status='FAILED', error_text='INVALID_LAB_CONTRACT', completed_at=now(),
                    dispatch_nonce_sha256=NULL
                WHERE job_id=%s AND status='QUEUED'
                """,
                (job_id,),
            )
            conn.commit()
            raise HTTPException(status_code=422, detail="invalid assistant-lab job") from exc
        if row["kind"] == "VIDEO_EVAL_CANARY":
            # Video evaluation is resident-only. Never reinterpret this typed
            # job as DDS3 in the public capability-dispatch path; leave it
            # queued for the authenticated loopback executor on Oracle.
            conn.rollback()
            return JSONResponse(
                {"job_id": str(job_id), "status": "QUEUED", "execution_path": "ORACLE_RESIDENT_ONLY"},
                status_code=202,
            )
        cur.execute(
            """
            UPDATE assistant_lab.job
            SET status='RUNNING', claimed_by=%s, claimed_at=now(), heartbeat_at=now(),
                attempts=attempts+1, error_text=NULL, completed_at=NULL
            WHERE job_id=%s AND status='QUEUED'
            RETURNING attempts, max_attempts
            """,
            (ASSISTANT_LAB_DISPATCHER, job_id),
        )
        claimed = cur.fetchone()
        if not claimed:
            conn.rollback()
            return JSONResponse({"job_id": str(job_id), "status": "RACE_RETRY"}, status_code=409)
        conn.commit()

    try:
        if row["kind"] == "NOOP":
            result = {"ok": True, "kind": "NOOP", "echo": payload, "contract": CONTRACT_VERSION}
        else:
            remote = _remote_dds3_config()
            if remote is None:
                raise DDSUnavailable("DDS3_REMOTE_URL_MISSING")
            operation = str(payload.get("operation") or "dd_table")
            result = compute_remote(
                payload,
                bearer_token=_vercel_oidc_token(request),
                config=remote,
            )
            result = verify_dds3_result(result, expected_operation=operation)
    except DDSUnavailable as exc:
        retryable = int(claimed["attempts"]) < int(claimed["max_attempts"])
        _assistant_lab_finish(
            job_id,
            status="FAILED",
            error_text=f"DDS_UNAVAILABLE:{str(exc)[:256]}",
            requeue=retryable,
        )
        logger.warning("assistant_lab_dispatch_dds_unavailable job_id=%s retryable=%s", job_id, retryable)
        return JSONResponse(
            {"job_id": str(job_id), "status": "QUEUED" if retryable else "FAILED", "detail": "DDS_UNAVAILABLE"},
            status_code=503,
        )
    except (LabContractError, ValueError) as exc:
        _assistant_lab_finish(job_id, status="FAILED", error_text=f"CONTRACT_ERROR:{str(exc)[:256]}")
        raise HTTPException(status_code=422, detail="assistant-lab computation rejected") from exc

    _assistant_lab_finish(job_id, status="COMPLETED", result=result)
    return JSONResponse({"job_id": str(job_id), "status": "COMPLETED", "result": result})


def _configuration_failure_category(exc: DatabaseConfigurationError) -> str:
    message = str(exc)
    if "not configured" in message: return "configuration_missing"
    if "complete PostgreSQL" in message or "valid PostgreSQL" in message: return "configuration_uri_invalid"
    if "principal" in message: return "configuration_principal"
    if "password" in message: return "configuration_password_missing"
    if "Neon endpoint" in message: return "configuration_endpoint"
    if "port" in message: return "configuration_port"
    if "database" in message: return "configuration_database"
    if "fragment" in message: return "configuration_fragment"
    if "TLS" in message: return "configuration_tls"
    if "channel binding" in message: return "configuration_channel_binding"
    return "configuration_error"


def _database_failure_category(exc: Exception) -> str:
    if isinstance(exc, DatabaseConfigurationError): return _configuration_failure_category(exc)
    text = str(exc).lower()
    if "password authentication failed" in text or "authentication failed" in text: return "authentication_failed"
    if "role" in text and "does not exist" in text: return "role_missing"
    if "database" in text and "does not exist" in text: return "database_missing"
    if "endpoint id is not specified" in text: return "endpoint_id_missing"
    if "could not translate host name" in text or "name or service not known" in text: return "dns_failed"
    if "network is unreachable" in text: return "network_unreachable"
    if "no route to host" in text: return "no_route_to_host"
    if "timeout" in text: return "connection_timeout"
    if "connection refused" in text: return "connection_refused"
    if "server closed the connection unexpectedly" in text: return "server_closed_connection"
    if "active endpoints limit exceeded" in text or "concurrently active endpoints" in text: return "active_endpoint_limit"
    if "remaining connection slots" in text: return "connection_limit"
    if "channel binding" in text: return "channel_binding_failed"
    if "ssl" in text or "certificate" in text: return "tls_failed"
    if isinstance(exc, psycopg.OperationalError): return "operational_error"
    return "database_error"


def database_service_unavailable_response(path: str, exc: Exception) -> JSONResponse:
    category = _database_failure_category(exc)
    logger.error("database_request_failed category=%s type=%s sqlstate=%s path=%s", category, type(exc).__name__, getattr(exc, "sqlstate", None), path)
    return apply_response_security_headers(path, JSONResponse({"detail": "service unavailable"}, status_code=503))  # type: ignore[return-value]


@app.exception_handler(psycopg.Error)
@app.exception_handler(DatabaseConfigurationError)
async def database_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return database_service_unavailable_response(request.url.path, exc)


@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT current_user AS principal, count(*) AS school_count FROM public.school WHERE stable_name = %s GROUP BY current_user", (EXPECTED_SCHOOL,))
            row = cur.fetchone()
    except Exception as exc:
        logger.error("database_health_check_failed category=%s type=%s sqlstate=%s", _database_failure_category(exc), type(exc).__name__, getattr(exc, "sqlstate", None))
        raise HTTPException(status_code=503, detail="service unavailable") from exc
    if not row or row["principal"] != EXPECTED_PRINCIPAL or row["school_count"] != 1:
        raise HTTPException(status_code=503, detail="service unavailable")
    return JSONResponse({"status": "ok"}, headers={"Cache-Control":"public, max-age=0, must-revalidate","Vercel-CDN-Cache-Control":"public, max-age=15, stale-while-revalidate=15"})


@app.get("/v1/overview", dependencies=[Depends(require_api_token)])
def overview() -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT s.school_id,s.stable_name,s.status,(SELECT count(*) FROM public.student st WHERE st.school_id=s.school_id) AS students,(SELECT count(*) FROM public.learning_group g WHERE g.school_id=s.school_id) AS groups,(SELECT count(*) FROM public.learning_interaction li WHERE li.school_id=s.school_id) AS interactions,(SELECT count(*) FROM public.media_asset ma WHERE ma.school_id=s.school_id) AS media_assets,(SELECT count(*) FROM public.transcript t WHERE t.school_id=s.school_id) AS transcripts,(SELECT count(*) FROM public.analysis_run ar WHERE ar.school_id=s.school_id) AS analysis_runs,(SELECT count(*) FROM public.artifact a WHERE a.school_id=s.school_id) AS artifacts FROM public.school s WHERE s.stable_name=%s", (EXPECTED_SCHOOL,))
        row=cur.fetchone()
    if not row: raise HTTPException(status_code=404, detail="school not found")
    return row


@app.get("/v1/students", dependencies=[Depends(require_api_token)])
def students(limit:int=Query(default=100,ge=1,le=500),offset:int=Query(default=0,ge=0))->list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT st.student_id,p.person_id,p.preferred_name,p.locale,p.timezone,st.current_status,st.school_joined_at,st.created_at FROM public.student st JOIN public.person p ON p.person_id=st.person_id JOIN public.school s ON s.school_id=st.school_id WHERE s.stable_name=%s ORDER BY p.preferred_name NULLS LAST,st.created_at LIMIT %s OFFSET %s",(EXPECTED_SCHOOL,limit,offset)); return cur.fetchall()


@app.get("/v1/media", dependencies=[Depends(require_api_token)])
def media(limit:int=Query(default=100,ge=1,le=500),offset:int=Query(default=0,ge=0))->list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ma.media_asset_id,ma.duration_seconds,ma.status,ma.created_at,t.transcript_id,t.transcript_type,t.language,t.status AS transcript_status,count(ts.transcript_segment_id) AS segment_count FROM public.media_asset ma JOIN public.school s ON s.school_id=ma.school_id LEFT JOIN public.transcript t ON t.media_asset_id=ma.media_asset_id LEFT JOIN public.transcript_segment ts ON ts.transcript_id=t.transcript_id WHERE s.stable_name=%s GROUP BY ma.media_asset_id,t.transcript_id ORDER BY ma.created_at DESC,t.created_at DESC NULLS LAST LIMIT %s OFFSET %s",(EXPECTED_SCHOOL,limit,offset)); return cur.fetchall()


@app.get("/v1/transcripts/{transcript_id}/segments", dependencies=[Depends(require_api_token)])
def transcript_segments(transcript_id:UUID,limit:int=Query(default=500,ge=1,le=2000),offset:int=Query(default=0,ge=0))->list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT ts.transcript_segment_id,ts.sequence_no,ts.start_seconds,ts.end_seconds,ts.speaker_label,ts.text,ts.confidence_class,ts.confidence_value FROM public.transcript_segment ts JOIN public.transcript t ON t.transcript_id=ts.transcript_id JOIN public.school s ON s.school_id=t.school_id WHERE ts.transcript_id=%s AND s.stable_name=%s ORDER BY ts.sequence_no LIMIT %s OFFSET %s",(transcript_id,EXPECTED_SCHOOL,limit,offset)); return cur.fetchall()
