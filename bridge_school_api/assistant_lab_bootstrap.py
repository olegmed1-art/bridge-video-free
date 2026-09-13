from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse

from bridge_contracts.bootstrap import BootstrapContractError, build_bootstrap_script, token_digest

from .db import connect

router = APIRouter()


def _bearer_token(authorization: str | None) -> str:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=404, detail="bootstrap ticket not found")
    token = authorization[len(prefix) :].strip()
    try:
        token_digest(token)
    except BootstrapContractError as exc:
        raise HTTPException(status_code=404, detail="bootstrap ticket not found") from exc
    return token


@router.post("/v1/assistant-lab/bootstrap", response_class=PlainTextResponse)
def assistant_lab_bootstrap(authorization: str | None = Header(default=None)) -> PlainTextResponse:
    """Redeem a short-lived bearer capability for a host-local bootstrap script."""
    token = _bearer_token(authorization)
    digest = token_digest(token)
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT assistant_lab.claim_bootstrap_ticket(%s) AS payload", (digest,))
        row = cur.fetchone()
        # connect() deliberately closes without an implicit commit. Persist the
        # one-time capability claim before returning any secret-bearing script.
        conn.commit()
    if not row or not isinstance(row.get("payload"), dict):
        raise HTTPException(status_code=404, detail="bootstrap ticket not found")
    try:
        script = build_bootstrap_script(row["payload"])
    except BootstrapContractError as exc:
        raise HTTPException(status_code=503, detail="bootstrap ticket is invalid") from exc
    return PlainTextResponse(
        script,
        status_code=200,
        media_type="text/x-shellscript",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


__all__ = ["router"]
