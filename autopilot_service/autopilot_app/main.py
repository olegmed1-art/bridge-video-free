"""Shadow-only FastAPI surface for the Vercel Workflows compatibility spike."""

from __future__ import annotations

import hmac
import os
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Header, HTTPException, status

from autopilot_app import __version__
from autopilot_app.workflows.shadow_wait import ShadowSignal


app = FastAPI(
    title="School Autopilot Controller — Shadow",
    version=__version__,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


def _require_shadow_authorization(authorization: str | None) -> None:
    expected = os.getenv("AUTOPILOT_SHADOW_SECRET", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SHADOW_RESUME_NOT_CONFIGURED",
        )

    supplied = authorization or ""
    wanted = f"Bearer {expected}"
    if not hmac.compare_digest(supplied, wanted):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SHADOW_AUTH_INVALID",
        )


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """Expose only non-sensitive compatibility state."""

    return {
        "status": "ok",
        "service": "school-autopilot-shadow",
        "service_version": __version__,
        "vercel_sdk_version": _package_version("vercel"),
        "shadow_only": True,
        "production_mutations_enabled": False,
        "workflow_start_enabled": False,
        "resume_configured": bool(os.getenv("AUTOPILOT_SHADOW_SECRET")),
    }


@app.post("/v1/shadow/resume", status_code=status.HTTP_202_ACCEPTED)
async def resume_shadow_wait(
    signal: ShadowSignal,
    authorization: str | None = Header(default=None),
    x_autopilot_hook_token: str | None = Header(default=None),
) -> dict[str, object]:
    """Resume one synthetic hook after a separate bearer check.

    The exact startup return contract remains disabled until the pinned Run
    object is observed by CI; no external workflow is launched by this commit.
    """

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
