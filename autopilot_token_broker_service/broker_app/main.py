"""Isolated, preview-only ingress for GitHub App installation tokens."""

from __future__ import annotations

import asyncio
import hmac
import os
import time
from typing import Literal

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from broker_app import __version__
from broker_app.github import (
    BrokerConfigurationError,
    BrokerContractError,
    BrokerRetryableError,
    REPOSITORY_FULL_NAME,
    issue_installation_token,
    load_config,
)


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


class InstallationTokenRequest(BaseModel):
    task_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    action_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository: Literal["olegmed1-art/bridge-video-free"]
    manifest_version: Literal[1]


def _broker_secret() -> str:
    value = os.getenv("AUTOPILOT_TOKEN_BROKER_SECRET", "")
    return value if len(value) >= 43 and len(value) <= 512 else ""


def _require_broker_authorization(authorization: str | None) -> None:
    expected = _broker_secret()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TOKEN_BROKER_NOT_CONFIGURED",
        )
    wanted = f"Bearer {expected}"
    if not hmac.compare_digest(authorization or "", wanted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="TOKEN_BROKER_AUTH_INVALID",
        )


def _broker_enabled() -> bool:
    return bool(
        _broker_secret()
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
    }


@app.post("/v1/github/installation-token")
async def installation_token(
    request: InstallationTokenRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _require_broker_authorization(authorization)
    try:
        config = load_config()
        token_payload = await asyncio.to_thread(
            issue_installation_token,
            config,
            now_epoch=int(time.time()),
        )
    except BrokerConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TOKEN_BROKER_NOT_CONFIGURED",
        ) from exc
    except BrokerRetryableError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GITHUB_TOKEN_TRANSIENT_ERROR",
        ) from exc
    except BrokerContractError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GITHUB_TOKEN_CONTRACT_ERROR",
        ) from exc

    return JSONResponse(
        {
            **token_payload,
            "task_key": request.task_key,
            "action_fingerprint": request.action_fingerprint,
            "manifest_version": request.manifest_version,
        },
        headers=NO_STORE_HEADERS,
    )
