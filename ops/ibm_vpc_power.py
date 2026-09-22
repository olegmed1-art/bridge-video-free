#!/usr/bin/env python3
"""Bounded IBM VPC instance status/action client.

The client authenticates with a scoped service-ID API key and refuses every
response whose instance ID or name differs from the pinned target. Mutating
actions also require an explicit one-shot authorization string supplied by a
separate fail-closed workload guard.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
API_VERSION = "2026-09-22"
ALLOWED_STATES = {
    "running",
    "stopped",
    "starting",
    "stopping",
    "restarting",
    "pending",
    "failed",
}
MUTATION_AUTHORIZATION = "IBM_POWER_MUTATION_AUTHORIZED=YES"
MAX_ERROR_BODY_BYTES = 64 * 1024
SAFE_PROVIDER_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class BoundedClientError(RuntimeError):
    """A stable, non-secret provider or target validation failure."""


def _safe_provider_error_detail(exc: urllib.error.HTTPError) -> tuple[str, str, str]:
    """Return only a bounded machine code and coarse body shape.

    Provider messages, request IDs, traces, and arbitrary response fields are
    deliberately ignored so diagnostics cannot echo secrets or metadata.
    """
    try:
        raw = exc.read(MAX_ERROR_BODY_BYTES + 1)
    except (AttributeError, OSError):
        return "", "unreadable", "unknown"
    if not raw:
        return "", "empty", "unknown"
    if len(raw) > MAX_ERROR_BODY_BYTES:
        return "", "oversize", "unknown"
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        stripped = raw.lstrip().lower()
        return "", "html" if stripped.startswith((b"<!doctype html", b"<html")) else "text", "unknown"
    if not isinstance(value, dict):
        return "", "json_other", "unknown"
    errors = value.get("errors")
    if isinstance(errors, list):
        shape = "json_errors"
    elif "error" in value:
        shape = "json_error"
    else:
        shape = "json_object"
    candidates = [value.get("code")]
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        candidates.append(errors[0].get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and SAFE_PROVIDER_CODE.fullmatch(candidate):
            return candidate.lower(), shape, "machine_code"
    rendered = json.dumps(value, separators=(",", ":")).lower()
    if "context-based restriction" in rendered or "context based restriction" in rendered:
        category = "context_restriction"
    elif "not authorized" in rendered or "not authorised" in rendered:
        category = "not_authorized"
    elif "access denied" in rendered or "access is denied" in rendered:
        category = "access_denied"
    elif "permission" in rendered or "forbidden" in rendered:
        category = "permission_denied"
    else:
        category = "unknown"
    return "", shape, category


@dataclass(frozen=True)
class Instance:
    instance_id: str
    name: str
    status: str


def _request_json(request: urllib.request.Request, *, timeout: int = 20) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        # The HTTP status is safe and useful for distinguishing a bad key,
        # missing IAM access, and a wrong provider endpoint. Never echo the
        # provider response body because it may contain request metadata.
        provider_code, provider_shape, provider_category = _safe_provider_error_detail(exc)
        suffix = (
            f"_code_{provider_code}"
            if provider_code
            else f"_shape_{provider_shape}_category_{provider_category}"
        )
        raise BoundedClientError(f"provider_http_{exc.code}{suffix}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BoundedClientError("provider_request_failed") from exc
    if len(raw) > 1024 * 1024:
        raise BoundedClientError("provider_response_too_large")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundedClientError("provider_response_invalid") from exc
    if not isinstance(value, dict):
        raise BoundedClientError("provider_response_invalid")
    return value


def obtain_token(api_key: str) -> str:
    if not api_key or any(char.isspace() for char in api_key):
        raise BoundedClientError("api_key_invalid")
    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": api_key,
        }
    ).encode()
    request = urllib.request.Request(
        IAM_URL,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        response = _request_json(request)
    except BoundedClientError as exc:
        raise BoundedClientError(f"iam_{exc}") from exc
    token = response.get("access_token")
    if not isinstance(token, str) or len(token) < 20 or any(c.isspace() for c in token):
        raise BoundedClientError("iam_token_invalid")
    return token


def _instance_url(region: str, instance_id: str, *, action: bool = False) -> str:
    if region != "eu-de":
        raise BoundedClientError("region_not_pinned")
    if not instance_id.startswith("02c7_"):
        raise BoundedClientError("instance_id_not_pinned_format")
    suffix = "/actions" if action else ""
    query = urllib.parse.urlencode({"version": API_VERSION, "generation": "2"})
    return f"https://{region}.iaas.cloud.ibm.com/v1/instances/{instance_id}{suffix}?{query}"


def parse_instance(value: dict, *, expected_id: str, expected_name: str) -> Instance:
    instance_id = value.get("id")
    name = value.get("name")
    status = value.get("status")
    if instance_id != expected_id:
        raise BoundedClientError("instance_id_mismatch")
    if name != expected_name:
        raise BoundedClientError("instance_name_mismatch")
    if status not in ALLOWED_STATES:
        raise BoundedClientError("instance_status_unknown")
    return Instance(instance_id=instance_id, name=name, status=status)


def read_instance(token: str, *, region: str, instance_id: str, name: str) -> Instance:
    request = urllib.request.Request(
        _instance_url(region, instance_id),
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        response = _request_json(request)
    except BoundedClientError as exc:
        raise BoundedClientError(f"vpc_{exc}") from exc
    return parse_instance(response, expected_id=instance_id, expected_name=name)


def create_action(
    token: str,
    *,
    region: str,
    instance_id: str,
    name: str,
    action: str,
    authorization: str,
) -> Instance:
    if action not in {"start", "stop"}:
        raise BoundedClientError("action_invalid")
    if authorization != MUTATION_AUTHORIZATION:
        raise BoundedClientError("mutation_authorization_absent")
    before = read_instance(token, region=region, instance_id=instance_id, name=name)
    allowed_from = {"start": "stopped", "stop": "running"}[action]
    if before.status != allowed_from:
        raise BoundedClientError(f"action_{action}_forbidden_from_{before.status}")
    payload = json.dumps({"type": action}, separators=(",", ":")).encode()
    request = urllib.request.Request(
        _instance_url(region, instance_id, action=True),
        data=payload,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    response = _request_json(request)
    return parse_instance(response, expected_id=instance_id, expected_name=name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "start", "stop"))
    parser.add_argument("--region", default="eu-de")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--instance-name", required=True)
    parser.add_argument("--mutation-authorization", default="")
    args = parser.parse_args(argv)
    try:
        token = obtain_token(os.environ.get("IBM_CLOUD_API_KEY", ""))
        if args.action == "status":
            instance = read_instance(
                token,
                region=args.region,
                instance_id=args.instance_id,
                name=args.instance_name,
            )
        else:
            instance = create_action(
                token,
                region=args.region,
                instance_id=args.instance_id,
                name=args.instance_name,
                action=args.action,
                authorization=args.mutation_authorization,
            )
    except BoundedClientError as exc:
        print(f"IBM_VPC_POWER_RESULT=REFUSED reason={exc}", file=sys.stderr)
        return 3
    print("IBM_VPC_POWER_RESULT=PASS")
    print(f"IBM_VPC_INSTANCE_ID={instance.instance_id}")
    print(f"IBM_VPC_INSTANCE_NAME={instance.name}")
    print(f"IBM_VPC_INSTANCE_STATUS={instance.status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
