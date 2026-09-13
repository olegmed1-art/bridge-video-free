"""Shadow-only FastAPI surface for the Vercel Workflows compatibility spike."""

from __future__ import annotations

import hmac
import os
import secrets
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from vercel import workflow

from autopilot_app import __version__
from autopilot_app.workflows.shadow_wait import ShadowSignal, shadow_wait_workflow


app = FastAPI(
    title="School Autopilot Controller — Shadow",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


class ShadowStartRequest(BaseModel):
    """Bounded input for one synthetic compatibility run."""

    task_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def _shadow_secret() -> str:
    value = os.getenv("AUTOPILOT_SHADOW_SECRET", "")
    return value if len(value) >= 32 else ""


def _shadow_configured() -> bool:
    return bool(_shadow_secret())


def _require_shadow_authorization(authorization: str | None) -> None:
    expected = _shadow_secret()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SHADOW_CONTROL_NOT_CONFIGURED",
        )

    supplied = authorization or ""
    wanted = f"Bearer {expected}"
    if not hmac.compare_digest(supplied, wanted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SHADOW_AUTH_INVALID",
        )


async def _start_shadow_workflow(*, task_id: str, hook_token: str) -> str:
    """Start through the exact public async API observed for vercel==0.10.0."""

    run = await workflow.start(
        shadow_wait_workflow,
        task_id=task_id,
        hook_token=hook_token,
    )
    run_id = run.run_id
    if not isinstance(run_id, str) or not run_id:
        raise RuntimeError("SHADOW_WORKFLOW_RUN_ID_INVALID")
    return run_id


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """Expose only non-sensitive compatibility state."""

    configured = _shadow_configured()
    return {
        "status": "ok",
        "service": "school-autopilot-shadow",
        "service_version": __version__,
        "vercel_sdk_version": _package_version("vercel"),
        "shadow_only": True,
        "production_mutations_enabled": False,
        "workflow_start_supported": True,
        "workflow_start_enabled": configured,
        "resume_configured": configured,
    }


@app.post("/v1/shadow/start", status_code=status.HTTP_202_ACCEPTED)
async def start_shadow_wait(
    request: ShadowStartRequest,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Start exactly one synthetic wait run.

    The returned hook token is an opaque capability delivered only to the
    already-authorized caller. It is not logged or persisted by this spike.
    """

    _require_shadow_authorization(authorization)
    hook_token = secrets.token_urlsafe(32)
    run_id = await _start_shadow_workflow(
        task_id=request.task_id,
        hook_token=hook_token,
    )
    return {
        "accepted": True,
        "task_id": request.task_id,
        "workflow_run_id": run_id,
        "hook_token": hook_token,
        "shadow_only": True,
    }


@app.post("/v1/shadow/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_shadow_wait(
    signal: ShadowSignal,
    authorization: str | None = Header(default=None),
    x_autopilot_hook_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Resume one synthetic hook after a separate bearer check."""

    _require_shadow_authorization(authorization)

    hook_token = (x_autopilot_hook_token or "").strip()
    if not (32 <= len(hook_token) <= 256):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SHADOW_HOOK_TOKEN_INVALID",
        )

    await signal.resume(hook_token)
    return {
        "accepted": True,
        "task_id": signal.task_id,
        "provider_event_id": signal.provider_event_id,
        "shadow_only": True,
    }
