from __future__ import annotations
import logging,os,secrets
from uuid import UUID
import psycopg
from fastapi import Depends,FastAPI,Header,HTTPException,Query,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field
from starlette.responses import Response
from .db import DatabaseConfigurationError,EXPECTED_PRINCIPAL,connect
from .dds3 import DDSUnavailable,solve_table
from .dds3.readiness import engine_readiness
EXPECTED_SCHOOL="Школа спортивного бриджа";logger=logging.getLogger("bridge_school_api");app=FastAPI(title="Bridge School API",version="0.3.0",docs_url=None,redoc_url=None,openapi_url=None)
def apply_response_security_headers(path:str,response:Response)->Response:
 response.headers["X-Content-Type-Options"]="nosniff";response.headers["Referrer-Policy"]="no-referrer"
 if path.startswith("/v1/"):
  response.headers["Cache-Control"]="private, no-store, max-age=0";response.headers["Pragma"]="no-cache"
  for h in ("Vercel-CDN-Cache-Control","CDN-Cache-Control"): response.headers.pop(h,None)
 return response
@app.middleware("http")
async def api_security_headers(request:Request,call_next):return apply_response_security_headers(request.url.path,await call_next(request))
def require_api_token(authorization:str|None=Header(default=None))->None:
 configured=os.environ.get("BRIDGE_API_TOKEN","")
 if not configured:raise HTTPException(503,"application API token is not configured")
 if not authorization or not authorization.startswith("Bearer "):raise HTTPException(401,"missing bearer token")
 if not secrets.compare_digest(authorization[7:],configured):raise HTTPException(403,"invalid bearer token")
class DDS3TableRequest(BaseModel):
 pbn:str=Field(min_length=1,max_length=512);dealer:str=Field(default="N",pattern="^[NESWnesw]$");vulnerability:str=Field(default="None",max_length=8)
@app.get("/dds3/readyz")
def dds3_readyz()->JSONResponse:
 r=engine_readiness();return JSONResponse(r,status_code=200 if r["status"]=="ready" else 503,headers={"Cache-Control":"no-store"})
@app.post("/v1/dds3/table",dependencies=[Depends(require_api_token)])
def dds3_table(request:DDS3TableRequest)->dict:
 try:return solve_table(pbn=request.pbn,dealer=request.dealer,vulnerability=request.vulnerability)
 except ValueError as exc:raise HTTPException(422,str(exc)) from exc
 except DDSUnavailable as exc:logger.error("dds3_request_failed category=dds_unavailable");raise HTTPException(503,"DDS_UNAVAILABLE") from exc
def _database_failure_category(exc:Exception)->str:
 if isinstance(exc,DatabaseConfigurationError):return "configuration_error"
 if isinstance(exc,psycopg.OperationalError):return "operational_error"
 return "database_error"
@app.exception_handler(psycopg.Error)
@app.exception_handler(DatabaseConfigurationError)
async def database_exception_handler(request:Request,exc:Exception)->JSONResponse:return apply_response_security_headers(request.url.path,JSONResponse({"detail":"service unavailable"},503))
@app.get("/healthz")
def healthz()->JSONResponse:
 try:
  with connect() as conn,conn.cursor() as cur:cur.execute("SELECT current_user AS principal,count(*) AS school_count FROM public.school WHERE stable_name=%s GROUP BY current_user",(EXPECTED_SCHOOL,));row=cur.fetchone()
 except Exception as exc:logger.error("database_health_check_failed category=%s",_database_failure_category(exc));raise HTTPException(503,"service unavailable") from exc
 if not row or row["principal"]!=EXPECTED_PRINCIPAL or row["school_count"]!=1:raise HTTPException(503,"service unavailable")
 return JSONResponse({"status":"ok"},headers={"Cache-Control":"public, max-age=0, must-revalidate","Vercel-CDN-Cache-Control":"public, max-age=15, stale-while-revalidate=15"})
@app.get("/v1/overview",dependencies=[Depends(require_api_token)])
def overview()->dict:
 with connect() as conn,conn.cursor() as cur:cur.execute("SELECT s.school_id,s.stable_name,s.status FROM public.school s WHERE s.stable_name=%s",(EXPECTED_SCHOOL,));row=cur.fetchone()
 if not row:raise HTTPException(404,"school not found")
 return row
@app.get("/v1/students",dependencies=[Depends(require_api_token)])
def students(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0))->list[dict]:
 with connect() as conn,conn.cursor() as cur:cur.execute("SELECT st.student_id,p.person_id,p.preferred_name,p.locale,p.timezone,st.current_status,st.school_joined_at,st.created_at FROM public.student st JOIN public.person p ON p.person_id=st.person_id JOIN public.school s ON s.school_id=st.school_id WHERE s.stable_name=%s ORDER BY p.preferred_name NULLS LAST,st.created_at LIMIT %s OFFSET %s",(EXPECTED_SCHOOL,limit,offset));return cur.fetchall()
@app.get("/v1/media",dependencies=[Depends(require_api_token)])
def media(limit:int=Query(100,ge=1,le=500),offset:int=Query(0,ge=0))->list[dict]:
 with connect() as conn,conn.cursor() as cur:cur.execute("SELECT ma.media_asset_id,ma.duration_seconds,ma.status,ma.created_at FROM public.media_asset ma JOIN public.school s ON s.school_id=ma.school_id WHERE s.stable_name=%s ORDER BY ma.created_at DESC LIMIT %s OFFSET %s",(EXPECTED_SCHOOL,limit,offset));return cur.fetchall()
@app.get("/v1/transcripts/{transcript_id}/segments",dependencies=[Depends(require_api_token)])
def transcript_segments(transcript_id:UUID,limit:int=Query(500,ge=1,le=2000),offset:int=Query(0,ge=0))->list[dict]:
 with connect() as conn,conn.cursor() as cur:cur.execute("SELECT ts.transcript_segment_id,ts.sequence_no,ts.start_seconds,ts.end_seconds,ts.speaker_label,ts.text,ts.confidence_class,ts.confidence_value FROM public.transcript_segment ts JOIN public.transcript t ON t.transcript_id=ts.transcript_id JOIN public.school s ON s.school_id=t.school_id WHERE ts.transcript_id=%s AND s.stable_name=%s ORDER BY ts.sequence_no LIMIT %s OFFSET %s",(transcript_id,EXPECTED_SCHOOL,limit,offset));return cur.fetchall()
