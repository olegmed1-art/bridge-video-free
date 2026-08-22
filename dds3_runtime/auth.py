from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient


@dataclass(frozen=True)
class VercelOIDCConfig:
    enabled: bool
    issuer_mode: str
    team_slug: str
    project_name: str
    team_id: str
    project_id: str
    environment: str

    @property
    def issuer(self) -> str:
        if self.issuer_mode == "global":
            return "https://oidc.vercel.com"
        return f"https://oidc.vercel.com/{self.team_slug}"

    @property
    def jwks_url(self) -> str:
        # Vercel's Team issuer is team-scoped, but its JWKS is served from the
        # OIDC origin root. Do not append the team slug to the JWKS path.
        return "https://oidc.vercel.com/.well-known/jwks"

    @property
    def audience(self) -> str:
        return f"https://vercel.com/{self.team_slug}"

    @property
    def subject(self) -> str:
        return (
            f"owner:{self.team_slug}:project:{self.project_name}:"
            f"environment:{self.environment}"
        )


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _oidc_config() -> VercelOIDCConfig:
    mode = os.getenv("DDS3_VERCEL_ISSUER_MODE", "team").strip().lower()
    if mode not in {"team", "global"}:
        mode = "invalid"
    return VercelOIDCConfig(
        enabled=_truthy(os.getenv("DDS3_TRUST_VERCEL_OIDC")),
        issuer_mode=mode,
        team_slug=os.getenv("DDS3_VERCEL_TEAM_SLUG", "").strip(),
        project_name=os.getenv("DDS3_VERCEL_PROJECT_NAME", "").strip(),
        team_id=os.getenv("DDS3_VERCEL_TEAM_ID", "").strip(),
        project_id=os.getenv("DDS3_VERCEL_PROJECT_ID", "").strip(),
        environment=os.getenv("DDS3_VERCEL_ENVIRONMENT", "production").strip(),
    )


@lru_cache(maxsize=4)
def _jwk_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True)


def _verify_vercel_oidc(token: str, cfg: VercelOIDCConfig) -> None:
    required = (
        cfg.team_slug,
        cfg.project_name,
        cfg.team_id,
        cfg.project_id,
        cfg.environment,
    )
    if not cfg.enabled or cfg.issuer_mode not in {"team", "global"} or not all(required):
        raise HTTPException(status_code=503, detail="runtime OIDC trust is not configured")

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise jwt.InvalidAlgorithmError("unexpected algorithm")
        signing_key = _jwk_client(cfg.jwks_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=cfg.audience,
            issuer=cfg.issuer,
            leeway=30,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
        if claims.get("sub") != cfg.subject:
            raise jwt.InvalidTokenError("subject mismatch")
        if claims.get("owner_id") != cfg.team_id:
            raise jwt.InvalidTokenError("owner mismatch")
        if claims.get("project_id") != cfg.project_id:
            raise jwt.InvalidTokenError("project mismatch")
        if claims.get("environment") != cfg.environment:
            raise jwt.InvalidTokenError("environment mismatch")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=403, detail="invalid bearer token") from exc


def auth(authorization: str | None = Header(default=None)) -> None:
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="missing bearer token")
    presented = authorization[len(prefix):]

    static_token = os.getenv("DDS3_RUNTIME_TOKEN", "")
    if static_token and secrets.compare_digest(presented, static_token):
        return

    cfg = _oidc_config()
    if not cfg.enabled:
        raise HTTPException(status_code=403, detail="invalid bearer token")
    _verify_vercel_oidc(presented, cfg)
