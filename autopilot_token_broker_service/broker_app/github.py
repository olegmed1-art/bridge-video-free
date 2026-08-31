"""Fail-closed GitHub App client and bounded draft-repair executor.

The installation credential never leaves this process. The only credentialed
operation is the exact server-side sequence required to create Git objects, one
new namespaced branch, and one draft pull request.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from broker_app.policy import DraftRepairRequest


GITHUB_API_URL = "https://api.github.com"
REPOSITORY_OWNER = "olegmed1-art"
REPOSITORY_NAME = "bridge-video-free"
REPOSITORY_FULL_NAME = f"{REPOSITORY_OWNER}/{REPOSITORY_NAME}"
REPOSITORY_API_PATH = f"/repos/{REPOSITORY_FULL_NAME}"
TOKEN_PERMISSIONS = {
    "checks": "read",
    "contents": "write",
    "pull_requests": "write",
}
TOKEN_RESPONSE_LIMIT_BYTES = 32_768
API_RESPONSE_LIMIT_BYTES = 65_536
HTTP_TIMEOUT_SECONDS = 15


class BrokerConfigurationError(RuntimeError):
    """The broker is missing or has invalid protected configuration."""


class BrokerContractError(RuntimeError):
    """GitHub returned a response outside the pinned broker contract."""


class BrokerRetryableError(RuntimeError):
    """A transient GitHub error may be retried by the bounded caller."""


class DraftRepairConflictError(RuntimeError):
    """Fresh GitHub state no longer matches exact request preconditions."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Prevent a credential-bearing request from changing origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise BrokerContractError("GITHUB_REDIRECT_REJECTED")


@dataclass(frozen=True)
class BrokerConfig:
    app_id: int
    installation_id: int
    private_key_pem: str


@dataclass(frozen=True)
class InstallationCredential:
    token: str
    expires_at: str


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
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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


def _validate_token_response(
    payload: Any, *, now_epoch: int
) -> InstallationCredential:
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
    return InstallationCredential(token=token, expires_at=expires_at)


def _open_json(
    request: urllib.request.Request,
    *,
    expected_status: int,
    response_limit: int,
    opener: Any | None,
    not_found_ok: bool = False,
    allow_list: bool = False,
) -> object | None:
    client = opener or urllib.request.build_opener(_RejectRedirects())
    try:
        with client.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            if response.status != expected_status or response.geturl() != request.full_url:
                raise BrokerContractError("GITHUB_RESPONSE_INVALID")
            raw = response.read(response_limit + 1)
    except urllib.error.HTTPError as exc:
        if not_found_ok and exc.code == 404:
            return None
        if exc.code in {408, 429} or 500 <= exc.code <= 599:
            raise BrokerRetryableError("GITHUB_TRANSIENT_ERROR") from exc
        if exc.code in {409, 422}:
            raise DraftRepairConflictError("GITHUB_PRECONDITION_FAILED") from exc
        raise BrokerContractError("GITHUB_HTTP_ERROR") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise BrokerRetryableError("GITHUB_TRANSIENT_ERROR") from exc
    if len(raw) > response_limit:
        raise BrokerContractError("GITHUB_RESPONSE_TOO_LARGE")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerContractError("GITHUB_RESPONSE_INVALID") from exc
    if not isinstance(payload, (dict, list) if allow_list else dict):
        raise BrokerContractError("GITHUB_RESPONSE_INVALID")
    return payload


def issue_installation_token(
    config: BrokerConfig,
    *,
    now_epoch: int,
    opener: Any | None = None,
) -> InstallationCredential:
    """Mint one repository- and permission-scoped internal credential."""

    app_jwt = build_app_jwt(config, now_epoch=now_epoch)
    path = f"/app/installations/{config.installation_id}/access_tokens"
    request = urllib.request.Request(
        f"{GITHUB_API_URL}{path}",
        data=_canonical_json(
            {
                "permissions": TOKEN_PERMISSIONS,
                "repositories": [REPOSITORY_NAME],
            }
        ),
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {app_jwt}",
            "Content-Type": "application/json",
            "User-Agent": "bridge-school-autopilot-token-broker/0.2",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    payload = _open_json(
        request,
        expected_status=201,
        response_limit=TOKEN_RESPONSE_LIMIT_BYTES,
        opener=opener,
    )
    return _validate_token_response(payload, now_epoch=now_epoch)


def _installation_request(
    credential: InstallationCredential,
    *,
    method: str,
    path: str,
    body: Mapping[str, object] | None = None,
) -> urllib.request.Request:
    if method not in {"GET", "POST"} or not path.startswith(f"{REPOSITORY_API_PATH}/"):
        raise BrokerContractError("GITHUB_OPERATION_NOT_ALLOWED")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {credential.token}",
        "User-Agent": "bridge-school-autopilot-bounded-repair/0.2",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if body is not None:
        if method != "POST":
            raise BrokerContractError("GITHUB_OPERATION_NOT_ALLOWED")
        headers["Content-Type"] = "application/json"
        data = _canonical_json(body)
    return urllib.request.Request(
        f"{GITHUB_API_URL}{path}", data=data, method=method, headers=headers
    )


def _api_json(
    credential: InstallationCredential,
    *,
    method: str,
    path: str,
    expected_status: int,
    body: Mapping[str, object] | None = None,
    opener: Any | None = None,
    not_found_ok: bool = False,
    allow_list: bool = False,
) -> object | None:
    request = _installation_request(
        credential, method=method, path=path, body=body
    )
    return _open_json(
        request,
        expected_status=expected_status,
        response_limit=API_RESPONSE_LIMIT_BYTES,
        opener=opener,
        not_found_ok=not_found_ok,
        allow_list=allow_list,
    )


def _sha(payload: object, *, error: str) -> str:
    if not isinstance(payload, str) or re.fullmatch(r"[0-9a-f]{40}", payload) is None:
        raise BrokerContractError(error)
    return payload


def _read_base_sha(
    credential: InstallationCredential, *, opener: Any | None
) -> str:
    payload = _api_json(
        credential,
        method="GET",
        path=f"{REPOSITORY_API_PATH}/git/ref/heads/main",
        expected_status=200,
        opener=opener,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("object"), dict):
        raise BrokerContractError("GITHUB_BASE_REF_INVALID")
    return _sha(payload["object"].get("sha"), error="GITHUB_BASE_REF_INVALID")


def _require_expected_base(
    credential: InstallationCredential,
    expected_sha: str,
    *,
    opener: Any | None,
) -> None:
    if _read_base_sha(credential, opener=opener) != expected_sha:
        raise DraftRepairConflictError("GITHUB_BASE_SHA_CHANGED")


def _base_commit_contract(
    payload: object, *, expected_sha: str
) -> tuple[str, str]:
    if (
        not isinstance(payload, dict)
        or payload.get("sha") != expected_sha
        or not isinstance(payload.get("tree"), dict)
        or not isinstance(payload.get("committer"), dict)
    ):
        raise BrokerContractError("GITHUB_BASE_COMMIT_INVALID")
    tree_sha = _sha(
        payload["tree"].get("sha"), error="GITHUB_BASE_COMMIT_INVALID"
    )
    date = payload["committer"].get("date")
    if not isinstance(date, str):
        raise BrokerContractError("GITHUB_BASE_COMMIT_INVALID")
    try:
        parsed = datetime.fromisoformat(date.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrokerContractError("GITHUB_BASE_COMMIT_INVALID") from exc
    if parsed.tzinfo is None:
        raise BrokerContractError("GITHUB_BASE_COMMIT_INVALID")
    return tree_sha, date


def _expected_ref(request: DraftRepairRequest) -> str:
    return f"refs/heads/{request.branch_name}"


def _validate_branch(
    payload: object,
    request: DraftRepairRequest,
    *,
    expected_commit_sha: str,
) -> None:
    if (
        not isinstance(payload, dict)
        or payload.get("ref") != _expected_ref(request)
        or not isinstance(payload.get("object"), dict)
        or payload["object"].get("sha") != expected_commit_sha
    ):
        raise DraftRepairConflictError("GITHUB_BRANCH_CHANGED")


def _pull_body(request: DraftRepairRequest) -> str:
    return (
        "Bounded Phase 3B canary/repair.\n\n"
        f"Task: `{request.task_key}`\n"
        f"Action fingerprint: `{request.action_fingerprint}`\n\n"
        "Draft only. Autopilot merge is forbidden."
    )


def _validate_pull(
    payload: object,
    request: DraftRepairRequest,
    *,
    expected_commit_sha: str,
) -> tuple[int, str]:
    if not isinstance(payload, dict):
        raise BrokerContractError("GITHUB_PULL_RESPONSE_INVALID")
    number = payload.get("number")
    html_url = payload.get("html_url")
    head = payload.get("head")
    base = payload.get("base")
    if (
        type(number) is not int
        or not 1 <= number <= 1_000_000
        or html_url != f"https://github.com/{REPOSITORY_FULL_NAME}/pull/{number}"
        or payload.get("state") != "open"
        or payload.get("draft") is not True
        or payload.get("title") != request.title
        or payload.get("body") != _pull_body(request)
        or not isinstance(head, dict)
        or head.get("ref") != request.branch_name
        or head.get("sha") != expected_commit_sha
        or not isinstance(base, dict)
        or base.get("ref") != "main"
    ):
        raise BrokerContractError("GITHUB_PULL_RESPONSE_INVALID")
    return number, html_url


def _existing_pull(
    credential: InstallationCredential,
    request: DraftRepairRequest,
    *,
    expected_commit_sha: str,
    opener: Any | None,
) -> tuple[int, str] | None:
    query = urllib.parse.urlencode(
        {
            "base": "main",
            "head": f"{REPOSITORY_OWNER}:{request.branch_name}",
            "per_page": 2,
            "state": "all",
        }
    )
    payload = _api_json(
        credential,
        method="GET",
        path=f"{REPOSITORY_API_PATH}/pulls?{query}",
        expected_status=200,
        opener=opener,
        allow_list=True,
    )
    if not isinstance(payload, list):
        raise BrokerContractError("GITHUB_PULL_LOOKUP_INVALID")
    if not payload:
        return None
    if len(payload) != 1:
        raise DraftRepairConflictError("GITHUB_PULL_LOOKUP_AMBIGUOUS")
    return _validate_pull(
        payload[0], request, expected_commit_sha=expected_commit_sha
    )


def _create_pull(
    credential: InstallationCredential,
    request: DraftRepairRequest,
    *,
    expected_commit_sha: str,
    opener: Any | None,
) -> tuple[int, str]:
    body = {
        "base": "main",
        "body": _pull_body(request),
        "draft": True,
        "head": request.branch_name,
        "title": request.title,
    }
    try:
        payload = _api_json(
            credential,
            method="POST",
            path=f"{REPOSITORY_API_PATH}/pulls",
            expected_status=201,
            body=body,
            opener=opener,
        )
    except DraftRepairConflictError:
        existing = _existing_pull(
            credential,
            request,
            expected_commit_sha=expected_commit_sha,
            opener=opener,
        )
        if existing is None:
            raise
        return existing
    return _validate_pull(
        payload, request, expected_commit_sha=expected_commit_sha
    )


def execute_bounded_draft_repair(
    config: BrokerConfig,
    request: DraftRepairRequest,
    *,
    now_epoch: int,
    opener: Any | None = None,
) -> dict[str, object]:
    """Execute or recover the only allowed Phase 3B GitHub write sequence."""

    credential = issue_installation_token(config, now_epoch=now_epoch, opener=opener)
    branch_payload = _api_json(
        credential,
        method="GET",
        path=f"{REPOSITORY_API_PATH}/git/ref/heads/{request.branch_name}",
        expected_status=200,
        opener=opener,
        not_found_ok=True,
    )
    if branch_payload is None:
        _require_expected_base(
            credential, request.expected_base_sha, opener=opener
        )

    commit_payload = _api_json(
        credential,
        method="GET",
        path=f"{REPOSITORY_API_PATH}/git/commits/{request.expected_base_sha}",
        expected_status=200,
        opener=opener,
    )
    base_tree_sha, base_date = _base_commit_contract(
        commit_payload, expected_sha=request.expected_base_sha
    )

    tree_entries: list[dict[str, str]] = []
    for change in request.changes:
        encoded_path = urllib.parse.quote(change.path, safe="/")
        query = urllib.parse.urlencode({"ref": request.expected_base_sha})
        content_path = f"{REPOSITORY_API_PATH}/contents/{encoded_path}?{query}"
        existing = _api_json(
            credential,
            method="GET",
            path=content_path,
            expected_status=200,
            opener=opener,
            not_found_ok=change.operation == "CREATE",
        )
        if change.operation == "CREATE":
            if existing is not None:
                raise DraftRepairConflictError("GITHUB_CREATE_PATH_EXISTS")
        elif (
            not isinstance(existing, dict)
            or existing.get("type") != "file"
            or existing.get("sha") != change.expected_blob_sha
        ):
            raise DraftRepairConflictError("GITHUB_UPDATE_BLOB_CHANGED")

        blob_payload = _api_json(
            credential,
            method="POST",
            path=f"{REPOSITORY_API_PATH}/git/blobs",
            expected_status=201,
            body={"content": change.content_utf8, "encoding": "utf-8"},
            opener=opener,
        )
        if not isinstance(blob_payload, dict):
            raise BrokerContractError("GITHUB_BLOB_RESPONSE_INVALID")
        tree_entries.append(
            {
                "path": change.path,
                "mode": "100644",
                "type": "blob",
                "sha": _sha(
                    blob_payload.get("sha"), error="GITHUB_BLOB_RESPONSE_INVALID"
                ),
            }
        )

    tree_payload = _api_json(
        credential,
        method="POST",
        path=f"{REPOSITORY_API_PATH}/git/trees",
        expected_status=201,
        body={"base_tree": base_tree_sha, "tree": tree_entries},
        opener=opener,
    )
    if not isinstance(tree_payload, dict):
        raise BrokerContractError("GITHUB_TREE_RESPONSE_INVALID")
    tree_sha = _sha(tree_payload.get("sha"), error="GITHUB_TREE_RESPONSE_INVALID")

    identity = {
        "name": "Bridge School Autopilot",
        "email": "noreply@github.com",
        "date": base_date,
    }
    new_commit_payload = _api_json(
        credential,
        method="POST",
        path=f"{REPOSITORY_API_PATH}/git/commits",
        expected_status=201,
        body={
            "message": f"autopilot: bounded repair {request.action_fingerprint}",
            "parents": [request.expected_base_sha],
            "tree": tree_sha,
            "author": identity,
            "committer": identity,
        },
        opener=opener,
    )
    if not isinstance(new_commit_payload, dict):
        raise BrokerContractError("GITHUB_COMMIT_RESPONSE_INVALID")
    new_commit_sha = _sha(
        new_commit_payload.get("sha"), error="GITHUB_COMMIT_RESPONSE_INVALID"
    )

    replayed = branch_payload is not None
    if branch_payload is not None:
        _validate_branch(
            branch_payload, request, expected_commit_sha=new_commit_sha
        )
    else:
        _require_expected_base(
            credential, request.expected_base_sha, opener=opener
        )
        try:
            ref_payload = _api_json(
                credential,
                method="POST",
                path=f"{REPOSITORY_API_PATH}/git/refs",
                expected_status=201,
                body={
                    "ref": _expected_ref(request),
                    "sha": new_commit_sha,
                },
                opener=opener,
            )
        except DraftRepairConflictError:
            ref_payload = _api_json(
                credential,
                method="GET",
                path=f"{REPOSITORY_API_PATH}/git/ref/heads/{request.branch_name}",
                expected_status=200,
                opener=opener,
            )
            replayed = True
        _validate_branch(
            ref_payload, request, expected_commit_sha=new_commit_sha
        )

    existing_pull = None
    if replayed:
        existing_pull = _existing_pull(
            credential,
            request,
            expected_commit_sha=new_commit_sha,
            opener=opener,
        )
    if existing_pull is None:
        number, html_url = _create_pull(
            credential,
            request,
            expected_commit_sha=new_commit_sha,
            opener=opener,
        )
        status_value = "created"
    else:
        number, html_url = existing_pull
        status_value = "existing"

    return {
        "status": status_value,
        "repository": REPOSITORY_FULL_NAME,
        "task_key": request.task_key,
        "action_fingerprint": request.action_fingerprint,
        "manifest_version": request.manifest_version,
        "base_sha": request.expected_base_sha,
        "branch_name": request.branch_name,
        "commit_sha": new_commit_sha,
        "pull_request_number": number,
        "pull_request_url": html_url,
        "draft": True,
        "replayed": replayed,
        "token_exposed": False,
        "merge_allowed": False,
        "production_mutation": False,
        "operation_count": 8 + 2 * len(request.changes),
    }
