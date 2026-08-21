"""Standalone DDS3 compute service: same engine for human table and position analysis."""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from bridge_school_api.dds3 import DDSUnavailable, compute
from bridge_school_api.dds3.readiness import engine_readiness

app = FastAPI(
    title="Bridge School DDS3 Runtime",
    version="1.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def auth(authorization: str | None = Header(default=None)) -> None:
    token = os.getenv("DDS3_RUNTIME_TOKEN", "")
    if not token:
        raise HTTPException(503, "runtime token not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    if not secrets.compare_digest(authorization[7:], token):
        raise HTTPException(403, "invalid bearer token")


class ComputeRequest(BaseModel):
    operation: str = Field(default="dd_table", max_length=32)
    pbn: str | None = Field(default=None, max_length=512)
    dealer: str = Field(default="N", pattern="^[NESWnesw]$")
    vulnerability: str = Field(default="None", max_length=8)
    deal: dict | None = None
    screenshot_observation: dict | None = None
    position: dict | None = None
    positions: list[dict] | None = Field(default=None, max_length=60)
    perspective: str | None = Field(default=None, max_length=2)


@app.get("/readyz")
def readyz():
    result = engine_readiness()
    if result["status"] != "ready":
        raise HTTPException(503, result)
    return result


@app.post("/v1/compute", dependencies=[Depends(auth)])
def run(req: ComputeRequest):
    payload = req.model_dump(exclude_none=True)
    try:
        return compute(payload)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    except DDSUnavailable as exc:
        raise HTTPException(503, "DDS_UNAVAILABLE") from exc
