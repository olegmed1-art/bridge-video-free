"""Preview-only ingress for bounded GitHub draft repairs."""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from broker_app import __version__
from broker_app.github import (
    BrokerConfigurationError,
    BrokerContractError,
    BrokerRetryableError,
    DraftRepairConflictError,
    REPOSITORY_FULL_NAME,
    execute_bounded_draft_repair,
    load_config,
)
from broker_app.policy import DraftRepairRequest


NO_STORE_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "Vary": "Authorization",
    "X-Content-Type-Options": "nosniff",
}

app = FastAPI(
    title="School Autopilot GitHub Token Broker",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.middleware("http")
async def add_no_store_headers(request, call_next):  # noqa: ANN001
    response = await call_next(request)
    for name, value in NO_STORE_HEADERS.items():
        response.headers[name] = value
    return response


def _broker_secret() -> str:
    value = os.getenv("AUTOPILOT_TOKEN_BROKER_SECRET", "")
    return value if len(value) >= 43 and len(value) <= 512 else ""


def _require_broker_authorization(authorization: str | None) -> None:
    expected = _broker_secret()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TOKEN_BROKER_NOT_CONFIGURED",
            headers=NO_STORE_HEADERS,
        )
    wanted = f"Bearer {expected}"
    if not hmac.compare_digest(authorization or "", wanted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="TOKEN_BROKER_AUTH_INVALID",
            headers=NO_STORE_HEADERS,
        )


def _require_preview_runtime() -> None:
    if os.getenv("VERCEL_ENV", "") != "preview":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TOKEN_BROKER_PREVIEW_ONLY",
            headers=NO_STORE_HEADERS,
        )


def _broker_enabled() -> bool:
    return bool(
        os.getenv("VERCEL_ENV", "") == "preview"
        and _broker_secret()
        and os.getenv("AUTOPILOT_GITHUB_APP_ID", "").strip()
        and os.getenv("AUTOPILOT_GITHUB_INSTALLATION_ID", "").strip()
        and os.getenv("AUTOPILOT_GITHUB_PRIVATE_KEY", "").strip()
    )


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    return {
        "status": "ok",
        "service": "school-autopilot-github-token-broker",
        "service_version": __version__,
        "repository": REPOSITORY_FULL_NAME,
        "preview_only": True,
        "production_mutations_enabled": False,
        "github_token_broker_enabled": _broker_enabled(),
        "raw_installation_token_exposed": False,
        "bounded_draft_executor_enabled": _broker_enabled(),
    }


@app.post("/v1/github/draft-repair")
async def draft_repair(
    request: DraftRepairRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _require_preview_runtime()
    _require_broker_authorization(authorization)
    try:
        config = load_config()
        result = await asyncio.to_thread(
            execute_bounded_draft_repair,
            config,
            request,
            now_epoch=int(time.time()),
        )
    except BrokerConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TOKEN_BROKER_NOT_CONFIGURED",
            headers=NO_STORE_HEADERS,
        ) from exc
    except BrokerRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GITHUB_TOKEN_TRANSIENT_ERROR",
            headers=NO_STORE_HEADERS,
        ) from exc
    except DraftRepairConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="DRAFT_REPAIR_PRECONDITION_FAILED",
            headers=NO_STORE_HEADERS,
        ) from exc
    except BrokerContractError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GITHUB_TOKEN_CONTRACT_ERROR",
            headers=NO_STORE_HEADERS,
        ) from exc

    return JSONResponse(
        result,
        headers=NO_STORE_HEADERS,
    )
