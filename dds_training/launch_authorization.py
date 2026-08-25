from __future__ import annotations

"""Fail-closed, one-time authorization receipts for any DDS execution.

The receipt is deliberately separate from the workflow file. It binds one
explicit launch request to one repository, actor, commit, scope, manifest hash
and high-entropy nonce. Verification consumes the receipt atomically, so
accidental retries cannot silently repeat an expensive or sealed operation.

Normal DDS execution remains workflow_dispatch-only. The sole exception is the
owner-only Pilot-10k operator command ``/dds3-pilot10k start``; that command may
issue a ``pilot_train`` receipt from an ``issue_comment`` event. No other scope
may use issue_comment authorization.
"""

import argparse
import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "dds-launch-authorization-v1"
APPROVAL_PHRASE = "ЭТАП-2-СТАРТ-ПОДТВЕРЖДАЮ"
DEFAULT_ACTOR = "olegmed1-art"
ALLOWED_SCOPES = {"pilot_train", "derived", "main_train", "validation", "sealed_test"}
PILOT_ISSUE_COMMAND = "/dds3-pilot10k start"
NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


class AuthorizationError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AuthorizationError(f"Invalid receipt timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise AuthorizationError("Receipt timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    if not path.is_file():
        raise AuthorizationError(f"Authorization-bound manifest is missing: {path}")
    return _sha256_bytes(path.read_bytes())


def _canonical(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_nonce(nonce: str) -> None:
    if not NONCE_RE.fullmatch(nonce):
        raise AuthorizationError(
            "authorization nonce must be 32-128 characters from A-Z, a-z, 0-9, '_' or '-'"
        )


def _validate_scope(scope: str) -> None:
    if scope not in ALLOWED_SCOPES:
        raise AuthorizationError(f"Unsupported authorization scope: {scope!r}")


def _validate_event(scope: str, event_name: str, authorization_command: str) -> None:
    if event_name == "workflow_dispatch":
        if authorization_command:
            raise AuthorizationError("workflow_dispatch authorization must not carry an issue command")
        return
    if event_name == "issue_comment":
        if scope != "pilot_train":
            raise AuthorizationError("issue_comment authorization is restricted to pilot_train")
        if authorization_command != PILOT_ISSUE_COMMAND:
            raise AuthorizationError("issue_comment Pilot-10k authorization command mismatch")
        return
    raise AuthorizationError(f"DDS execution is not allowed from event {event_name!r}")


def issue_receipt(
    *,
    out_path: Path,
    repository: str,
    ref_name: str,
    commit_sha: str,
    expected_commit_sha: str,
    actor: str,
    triggering_actor: str,
    scope: str,
    manifest_path: Path,
    nonce: str,
    approval_phrase: str,
    ttl_minutes: int = 20,
    now: datetime | None = None,
    expected_actor: str = DEFAULT_ACTOR,
    event_name: str = "workflow_dispatch",
    authorization_command: str = "",
) -> dict[str, Any]:
    _validate_scope(scope)
    _validate_nonce(nonce)
    _validate_event(scope, event_name, authorization_command)
    if approval_phrase != APPROVAL_PHRASE:
        raise AuthorizationError("Exact explicit approval phrase is missing")
    if actor != expected_actor or triggering_actor != expected_actor:
        raise AuthorizationError(
            f"Only {expected_actor!r} may issue a DDS launch receipt; actor={actor!r}, triggering_actor={triggering_actor!r}"
        )
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise AuthorizationError("Actual commit SHA must contain exactly 40 lowercase hexadecimal characters")
    if not hmac.compare_digest(commit_sha, expected_commit_sha):
        raise AuthorizationError("Expected commit does not match the checked-out commit")
    if not repository or "/" not in repository:
        raise AuthorizationError("Repository must be in owner/name form")
    if not ref_name:
        raise AuthorizationError("Ref name is required")
    if ttl_minutes < 1 or ttl_minutes > 60:
        raise AuthorizationError("Receipt TTL must be between 1 and 60 minutes")

    issued = (now or _utc_now()).astimezone(timezone.utc)
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "repository": repository,
        "ref_name": ref_name,
        "commit_sha": commit_sha,
        "actor": actor,
        "triggering_actor": triggering_actor,
        "scope": scope,
        "manifest_path": manifest_path.name,
        "manifest_sha256": file_sha256(manifest_path),
        "nonce_sha256": _sha256_bytes(nonce.encode("utf-8")),
        "issued_at": _iso(issued),
        "expires_at": _iso(issued + timedelta(minutes=ttl_minutes)),
        "event_name": event_name,
        "authorization_command": authorization_command,
    }
    payload["receipt_id"] = _sha256_bytes(_canonical(payload))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_suffix(out_path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(out_path)
    return payload


def load_receipt(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorizationError(f"Cannot read authorization receipt {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise AuthorizationError(f"Receipt schema must be {SCHEMA!r}")
    receipt_id = str(data.get("receipt_id", ""))
    unsigned = dict(data)
    unsigned.pop("receipt_id", None)
    expected = _sha256_bytes(_canonical(unsigned))
    if not hmac.compare_digest(receipt_id, expected):
        raise AuthorizationError("Receipt content hash is invalid")
    return data


def verify_and_consume(
    *,
    receipt_path: Path,
    manifest_path: Path,
    nonce: str,
    scope: str,
    repository: str,
    ref_name: str,
    commit_sha: str,
    actor: str,
    triggering_actor: str,
    event_name: str,
    consume_dir: Path,
    now: datetime | None = None,
    expected_actor: str = DEFAULT_ACTOR,
    authorization_command: str = "",
) -> dict[str, Any]:
    _validate_scope(scope)
    _validate_nonce(nonce)
    _validate_event(scope, event_name, authorization_command)
    receipt = load_receipt(receipt_path)
    checks = {
        "repository": repository,
        "ref_name": ref_name,
        "commit_sha": commit_sha,
        "actor": actor,
        "triggering_actor": triggering_actor,
        "scope": scope,
        "event_name": event_name,
        "authorization_command": authorization_command,
    }
    for key, actual in checks.items():
        expected = str(receipt.get(key, ""))
        if not hmac.compare_digest(expected, str(actual)):
            raise AuthorizationError(f"Receipt {key} mismatch: expected {expected!r}, got {actual!r}")
    if actor != expected_actor or triggering_actor != expected_actor:
        raise AuthorizationError("DDS launch actor is not the configured repository owner")
    if not hmac.compare_digest(receipt["nonce_sha256"], _sha256_bytes(nonce.encode("utf-8"))):
        raise AuthorizationError("Authorization nonce does not match the receipt")
    manifest_hash = file_sha256(manifest_path)
    if not hmac.compare_digest(str(receipt["manifest_sha256"]), manifest_hash):
        raise AuthorizationError("Task manifest changed after authorization")

    current = (now or _utc_now()).astimezone(timezone.utc)
    issued = _parse_time(str(receipt["issued_at"]))
    expires = _parse_time(str(receipt["expires_at"]))
    if current < issued - timedelta(seconds=30):
        raise AuthorizationError("Receipt was issued in the future")
    if current > expires:
        raise AuthorizationError("Authorization receipt has expired")

    consume_dir.mkdir(parents=True, exist_ok=True)
    marker = consume_dir / f"{receipt['receipt_id']}.consumed"
    try:
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise AuthorizationError("Authorization receipt has already been consumed") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({
            "receipt_id": receipt["receipt_id"],
            "consumed_at": _iso(current),
            "scope": scope,
            "commit_sha": commit_sha,
            "event_name": event_name,
        }, sort_keys=True) + "\n")
    return receipt


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise AuthorizationError(f"Required environment value is missing: {name}")
    return value


def _event_command(event_name: str) -> str:
    if event_name != "issue_comment":
        return ""
    path = Path(_env("GITHUB_EVENT_PATH"))
    try:
        event = json.loads(path.read_text(encoding="utf-8"))
        return str(event["comment"]["body"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AuthorizationError("Cannot resolve issue_comment authorization command") from exc


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Issue or verify one-time DDS launch authorization receipts")
    sp = p.add_subparsers(dest="command", required=True)

    q = sp.add_parser("issue")
    q.add_argument("--out", required=True)
    q.add_argument("--scope", required=True, choices=sorted(ALLOWED_SCOPES))
    q.add_argument("--manifest", required=True)
    q.add_argument("--nonce", required=True)
    q.add_argument("--approval-phrase", required=True)
    q.add_argument("--expected-commit", required=True)
    q.add_argument("--ttl-minutes", type=int, default=20)

    q = sp.add_parser("verify")
    q.add_argument("--receipt", required=True)
    q.add_argument("--scope", required=True, choices=sorted(ALLOWED_SCOPES))
    q.add_argument("--manifest", required=True)
    q.add_argument("--nonce", required=True)
    q.add_argument("--consume-dir", required=True)
    return p


def main() -> None:
    args = parser().parse_args()
    try:
        event_name = _env("GITHUB_EVENT_NAME")
        command = os.environ.get("DDS_AUTHORIZATION_COMMAND", "") or _event_command(event_name)
        if args.command == "issue":
            result = issue_receipt(
                out_path=Path(args.out),
                repository=_env("GITHUB_REPOSITORY"),
                ref_name=_env("GITHUB_REF_NAME"),
                commit_sha=_env("GITHUB_SHA"),
                expected_commit_sha=args.expected_commit,
                actor=_env("GITHUB_ACTOR"),
                triggering_actor=_env("GITHUB_TRIGGERING_ACTOR"),
                scope=args.scope,
                manifest_path=Path(args.manifest),
                nonce=args.nonce,
                approval_phrase=args.approval_phrase,
                ttl_minutes=args.ttl_minutes,
                event_name=event_name,
                authorization_command=command,
            )
        else:
            result = verify_and_consume(
                receipt_path=Path(args.receipt),
                manifest_path=Path(args.manifest),
                nonce=args.nonce,
                scope=args.scope,
                repository=_env("GITHUB_REPOSITORY"),
                ref_name=_env("GITHUB_REF_NAME"),
                commit_sha=_env("GITHUB_SHA"),
                actor=_env("GITHUB_ACTOR"),
                triggering_actor=_env("GITHUB_TRIGGERING_ACTOR"),
                event_name=event_name,
                authorization_command=command,
                consume_dir=Path(args.consume_dir),
            )
        print(json.dumps({
            "ok": True,
            "receipt_id": result["receipt_id"],
            "scope": result["scope"],
            "commit_sha": result["commit_sha"],
            "expires_at": result["expires_at"],
            "event_name": result["event_name"],
        }, ensure_ascii=False, indent=2))
    except AuthorizationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=os.sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
