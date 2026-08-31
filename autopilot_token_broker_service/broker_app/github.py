"""Fail-closed GitHub App installation-token client.

The GitHub App private key is consumed only inside this isolated service.  The
Oracle worker receives a repository-scoped installation token that GitHub
expires after one hour; it never receives the App private key.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


GITHUB_API_URL = "https://api.github.com"
REPOSITORY_OWNER = "olegmed1-art"
REPOSITORY_NAME = "bridge-video-free"
REPOSITORY_FULL_NAME = f"{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
TOKEN_PERMISSIONS = {
    "checks": "read",
    "contents": "write",
    "pull_requests": "write",
}
TOKEN_RESPONSE_LIMIT_BYTES = 32_768
HTTP_TIMEOUT_SECONDS = 15


class BrokerConfigurationError(RuntimeError):
    """The broker is missing or has invalid protected configuration."""


class BrokerContractError(RuntimeError):
    """GitHub returned a response outside the pinned broker contract."""


class BrokerRetryableError(RuntimeError):
    """A transient GitHub error may be retried by the bounded caller."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent a credential-bearing request from changing origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise BrokerContractError("GITHUB_TOKEN_REDIRECT_REJECTED")


@dataclass(frozen=True)
class BrokerConfig:
    app_id: int
    installation_id: int
    private_key_pem: str


def _positive_identifier(raw: str, *, name: str) -> int:
    value = raw.strip()
    if not value.isascii() or not value.isdecimal():
        raise BrokerConfigurationError(f"{name}_INVALID")
    identifier = int(value)
    if not 1 <= identifier <= 2**63 - 1:
        raise BrokerConfigurationError(f"{name}_INVALID")
    return identifier


def _load_rsa_private_key(private_key_pem: str) -> rsa.RSAPrivateKey:
    if not 1_000 <= len(private_key_pem) <= 16_384 or "\x00" in private_key_pem:
        raise BrokerConfigurationError("GITHUB_APP_PRIVATE_KEY_INVALID")
    try:
        key = serialization.load_pem_private_key(
            private_key_pem.encode("ascii"),
            password=None,
        )
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise BrokerConfigurationError("GITHUB_APP_PRIVATE_KEY_INVALID") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2_048:
        raise BrokerConfigurationError("GITHUB_APP_PRIVATE_KEY_INVALID")
    return key


def load_config(environ: Mapping[str, str] | None = None) -> BrokerConfig:
    source = os.environ if environ is None else environ
    app_id = _positive_identifier(
        source.get("AUTOPILOT_GITHUB_APP_ID", ""),
        name="GITHUB_APP_ID",
    )
    installation_id = _positive_identifier(
        source.get("AUTOPILOT_GITHUB_INSTALLATION_ID", ""),
        name="GITHUB_INSTALLATION_ID",
    )
    private_key_pem = source.get("AUTOPILOT_GITHUB_PRIVATE_KEY", "").strip()
    _load_rsa_private_key(private_key_pem)
    return BrokerConfig(
        app_id=app_id,
        installation_id=installation_id,
        private_key_pem=private_key_pem,
    )


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _canonical_json(value: Mapping[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def build_app_jwt(config: BrokerConfig, *, now_epoch: int) -> str:
    """Build GitHub's short-lived App JWT without exposing key material."""

    if not 1_700_000_000 <= now_epoch <= 4_102_444_800:
        raise BrokerConfigurationError("CLOCK_INVALID")
    header = _base64url(_canonical_json({"alg": "RS256", "typ": "JWT"}))
    payload = _base64url(
        _canonical_json(
            {
                "exp": now_epoch + 540,
                "iat": now_epoch - 60,
                "iss": config.app_id,
            }
        )
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = _load_rsa_private_key(config.private_key_pem).sign(
        signing_input,
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{header}.{payload}.{_base64url(signature)}"


def _validate_token_response(payload: Any, *, now_epoch: int) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise BrokerContractError("GITHUB_TOKEN_RESPONSE_INVALID")
    token = payload.get("token")
    expires_at = payload.get("expires_at")
    repositories = payload.get("repositories")
    permissions = payload.get("permissions")
    if (
        not isinstance(token, str)
        or not 20 <= len(token) <= 1_024
        or not token.startswith("ghs_")
        or any(character.isspace() for character in token)
        or not isinstance(expires_at, str)
        or not isinstance(repositories, list)
        or len(repositories) != 1
        or not isinstance(repositories[0], dict)
        or repositories[0].get("full_name") != REPOSITORY_FULL_NAME
        or not isinstance(permissions, dict)
    ):
        raise BrokerContractError("GITHUB_TOKEN_RESPONSE_INVALID")

    for permission, access in TOKEN_PERMISSIONS.items():
        if permissions.get(permission) != access:
            raise BrokerContractError("GITHUB_TOKEN_PERMISSIONS_INVALID")
    unexpected_permissions = set(permissions) - set(TOKEN_PERMISSIONS) - {"metadata"}
    if unexpected_permissions or permissions.get("metadata") not in {None, "read"}:
        raise BrokerContractError("GITHUB_TOKEN_PERMISSIONS_INVALID")

    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrokerContractError("GITHUB_TOKEN_EXPIRY_INVALID") from exc
    if expiry.tzinfo is None:
        raise BrokerContractError("GITHUB_TOKEN_EXPIRY_INVALID")
    lifetime_seconds = int(expiry.astimezone(timezone.utc).timestamp()) - now_epoch
    if not 60 <= lifetime_seconds <= 3_900:
        raise BrokerContractError("GITHUB_TOKEN_EXPIRY_INVALID")
    return {"token": token, "expires_at": expires_at}


def issue_installation_token(
    config: BrokerConfig,
    *,
    now_epoch: int,
    opener: Any | None = None,
) -> dict[str, object]:
    """Request one repository- and permission-scoped installation token."""

    app_jwt = build_app_jwt(config, now_epoch=now_epoch)
    url = (
        f"{GITHUB_API_URL}/app/installations/"
        f"{config.installation_id}/access_tokens"
    )
    body = _canonical_json(
        {
            "permissions": TOKEN_PERMISSIONS,
            "repositories": [REPOSITORY_NAME],
        }
    )
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {app_jwt}",
            "Content-Type": "application/json",
            "User-Agent": "bridge-school-autopilot-token-broker/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    client = opener or urllib.request.build_opener(_RejectRedirects())
    try:
        with client.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != 201 or response.geturl() != url:
                raise BrokerContractError("GITHUB_TOKEN_RESPONSE_INVALID")
            raw = response.read(TOKEN_RESPONSE_LIMIT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code in {408, 429} or 500 <= exc.code <= 599:
            raise BrokerRetryableError("GITHUB_TOKEN_TRANSIENT_ERROR") from exc
        raise BrokerContractError("GITHUB_TOKEN_HTTP_ERROR") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BrokerRetryableError("GITHUB_TOKEN_TRANSIENT_ERROR") from exc
    if len(raw) > TOKEN_RESPONSE_LIMIT_BYTES:
        raise BrokerContractError("GITHUB_TOKEN_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerContractError("GITHUB_TOKEN_RESPONSE_INVALID") from exc
    validated = _validate_token_response(payload, now_epoch=now_epoch)
    return {
        **validated,
        "repository": REPOSITORY_FULL_NAME,
        "permissions": dict(TOKEN_PERMISSIONS),
        "token_type": "github_app_installation",
    }
