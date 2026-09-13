"""Preview-only ingress for bounded GitHub draft repairs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse

from broker_app import __version__
from broker_app.github import (
    BROKER_POLICY_VERSION,
    BrokerConfigurationError,
    BrokerContractError,
    BrokerRetryableError,
    DraftRepairConflictError,
    REPOSITORY_FULL_NAME,
    broker_policy_sha256,
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
        and _deployment_provenance() is not None
        and _broker_secret()
        and os.getenv("AUTOPILOT_GITHUB_APP_ID", "").strip()
        and os.getenv("AUTOPILOT_GITHUB_INSTALLATION_ID", "").strip()
        and os.getenv("AUTOPILOT_GITHUB_PRIVATE_KEY", "").strip()
    )


def _source_revision() -> str:
    """Return Vercel's immutable deployment revision, never a user attestation."""

    value = os.getenv("VERCEL_GIT_COMMIT_SHA", "").strip()
    if len(value) == 40 and all(character in "0123456789abcdef" for character in value):
        return value
    return "UNATTESTED"


def _artifact_sha256() -> str:
    """Hash runtime code and the fully resolved dependency manifest."""

    service_root = Path(__file__).resolve().parent.parent
    root = service_root / "broker_app"
    digest = hashlib.sha256()
    paths = [*root.glob("*.py"), service_root / "uv.lock"]
    for path in sorted(
        paths, key=lambda item: item.relative_to(service_root).as_posix()
    ):
        data = path.read_bytes()
        digest.update(path.relative_to(service_root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _deployment_provenance() -> dict[str, str] | None:
    """Bind the platform source revision to the loaded artifact and policy."""

    source_sha = _source_revision()
    if source_sha == "UNATTESTED":
        return None
    statement = {
        "artifact_sha256": _artifact_sha256(),
        "policy_sha256": broker_policy_sha256(),
        "policy_version": BROKER_POLICY_VERSION,
        "source_sha": source_sha,
    }
    encoded = json.dumps(statement, sort_keys=True, separators=(",", ":")).encode()
    return {**statement, "provenance_sha256": hashlib.sha256(encoded).hexdigest()}


def _require_source_attestation() -> None:
    if _deployment_provenance() is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TOKEN_BROKER_SOURCE_UNATTESTED",
            headers=NO_STORE_HEADERS,
        )


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    provenance = _deployment_provenance()
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
        "broker_policy_version": BROKER_POLICY_VERSION,
        "source_revision": _source_revision(),
        "source_attested": _source_revision() != "UNATTESTED",
        "artifact_sha256": _artifact_sha256(),
        "artifact_attested": provenance is not None,
        "policy_sha256": broker_policy_sha256(),
        "provenance_sha256": provenance["provenance_sha256"] if provenance else "UNATTESTED",
        "merge_endpoint_enabled": False,
        "ref_update_delete_enabled": False,
        "actions_endpoint_enabled": False,
        "deployments_endpoint_enabled": False,
    }


@app.post("/v1/github/draft-repair")
async def draft_repair(
    request: DraftRepairRequest,
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    _require_preview_runtime()
    _require_source_attestation()
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

    provenance = _deployment_provenance()
    if provenance is None:  # The preflight above is fail-closed; retain type safety.
        raise HTTPException(status_code=503, detail="TOKEN_BROKER_SOURCE_UNATTESTED")
    result = {
        **result,
        "broker_policy_version": BROKER_POLICY_VERSION,
        "broker_source_sha": provenance["source_sha"],
        "broker_artifact_sha256": provenance["artifact_sha256"],
        "broker_policy_sha256": provenance["policy_sha256"],
        "broker_provenance_sha256": provenance["provenance_sha256"],
    }
    return JSONResponse(
        result,
        headers=NO_STORE_HEADERS,
    )
