from __future__ import annotations

import pytest
from fastapi import HTTPException

from dds3_runtime import auth as auth_module


TEAM_SLUG = "example-team"
TEAM_ISSUER = f"https://oidc.vercel.com/{TEAM_SLUG}"
GLOBAL_ISSUER = "https://oidc.vercel.com"


def _config(mode: str) -> auth_module.VercelOIDCConfig:
    return auth_module.VercelOIDCConfig(
        enabled=True,
        issuer_mode=mode,
        team_slug=TEAM_SLUG,
        project_name="example-project",
        team_id="team_test",
        project_id="prj_test",
        environment="production",
    )


def _verified_claims(issuer: str) -> dict[str, str]:
    return {
        "iss": issuer,
        "aud": f"https://vercel.com/{TEAM_SLUG}",
        "sub": f"owner:{TEAM_SLUG}:project:example-project:environment:production",
        "owner_id": "team_test",
        "project_id": "prj_test",
        "environment": "production",
        "exp": "future",
        "iat": "past",
    }


@pytest.mark.parametrize("issuer", [TEAM_ISSUER, GLOBAL_ISSUER])
def test_auto_mode_accepts_both_official_vercel_issuers(monkeypatch, issuer: str) -> None:
    requested_jwks: list[str] = []

    class SigningKey:
        key = "public-key"

    class Client:
        def get_signing_key_from_jwt(self, token: str) -> SigningKey:
            assert token == issuer
            return SigningKey()

    def fake_jwk_client(url: str) -> Client:
        requested_jwks.append(url)
        return Client()

    def fake_decode(token: str, *args, **kwargs):
        if kwargs.get("options", {}).get("verify_signature") is False:
            return {"iss": token}
        assert kwargs["issuer"] == issuer
        assert kwargs["audience"] == f"https://vercel.com/{TEAM_SLUG}"
        return _verified_claims(issuer)

    monkeypatch.setattr(auth_module.jwt, "get_unverified_header", lambda token: {"alg": "RS256"})
    monkeypatch.setattr(auth_module.jwt, "decode", fake_decode)
    monkeypatch.setattr(auth_module, "_jwk_client", fake_jwk_client)

    auth_module._verify_vercel_oidc(issuer, _config("auto"))

    assert requested_jwks == [f"{issuer}/.well-known/jwks"]


def test_team_mode_rejects_global_issuer(monkeypatch) -> None:
    monkeypatch.setattr(auth_module.jwt, "get_unverified_header", lambda token: {"alg": "RS256"})
    monkeypatch.setattr(
        auth_module.jwt,
        "decode",
        lambda token, *args, **kwargs: {"iss": GLOBAL_ISSUER},
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_module._verify_vercel_oidc("token", _config("team"))

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "invalid bearer token"


def test_auto_mode_still_rejects_untrusted_issuer(monkeypatch) -> None:
    monkeypatch.setattr(auth_module.jwt, "get_unverified_header", lambda token: {"alg": "RS256"})
    monkeypatch.setattr(
        auth_module.jwt,
        "decode",
        lambda token, *args, **kwargs: {"iss": "https://attacker.example"},
    )

    with pytest.raises(HTTPException) as exc_info:
        auth_module._verify_vercel_oidc("token", _config("auto"))

    assert exc_info.value.status_code == 403
