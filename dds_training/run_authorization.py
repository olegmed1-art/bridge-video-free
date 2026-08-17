from __future__ import annotations

"""Fail-closed authorization contract for mass DDS evaluation.

A valid authorization is created only after an explicit user decision.  The
committed/runtime manifest contains no plaintext approval token; it stores only
the token SHA-256 and binds the approval to one algorithm version, corpus,
prediction file, stage and exact split set.  The token is supplied separately at
execution time.

This module never starts DDS and never issues an approval automatically.
"""

import argparse
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from checkpointing import sha256_file
from config import ALGORITHM_VERSION

AUTH_SCHEMA = "dds-run-authorization-v1"
APPROVED_STATUS = "approved"
CONFIRM_TOKEN = "YES"


class AuthorizationError(RuntimeError):
    """Raised when a DDS run is not covered by a valid explicit authorization."""


@dataclass(frozen=True)
class AuthorizationContext:
    authorization_id: str
    context_sha256: str
    stage: str
    splits: tuple[str, ...]
    corpus_sha256: str
    predictions_sha256: str
    expires_at: str
    allow_sealed: bool
    max_tasks: int | None


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_utc(value: object, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise AuthorizationError(f"Authorization field {field!r} is required")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuthorizationError(f"Authorization field {field!r} is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise AuthorizationError(f"Authorization field {field!r} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def load_authorization(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuthorizationError(f"DDS authorization file is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise AuthorizationError(f"DDS authorization file is invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise AuthorizationError("DDS authorization must be a JSON object")
    return data


def corpus_sha256(work: Path) -> str:
    summary = work / "corpus_summary.json"
    if not summary.is_file():
        raise AuthorizationError(f"Corpus summary is missing: {summary}")
    try:
        data = json.loads(summary.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AuthorizationError(f"Corpus summary is invalid JSON: {summary}") from exc
    value = str(data.get("raw_sha256", "")).strip()
    if len(value) != 64:
        raise AuthorizationError(f"Corpus summary has no valid raw_sha256: {summary}")
    return value


def build_authorization(
    *,
    authorization_id: str,
    approval_token: str,
    stage: str,
    splits: Iterable[str],
    corpus_hash: str,
    predictions_hash: str,
    not_before: str,
    expires_at: str,
    allow_sealed: bool = False,
    max_tasks: int | None = None,
    issued_by: str = "explicit_user_command",
) -> dict:
    """Build a manifest for an external explicit-approval process.

    The helper is intentionally pure and is used by tests and by the assistant
    after a user command.  It does not write files or start a workflow.
    """
    clean_id = authorization_id.strip()
    if not clean_id:
        raise ValueError("authorization_id is required")
    clean_token = approval_token.strip()
    if len(clean_token) < 16:
        raise ValueError("approval_token must contain at least 16 characters")
    clean_splits = sorted({str(value).strip() for value in splits if str(value).strip()})
    if not clean_splits:
        raise ValueError("At least one split is required")
    if max_tasks is not None and int(max_tasks) <= 0:
        raise ValueError("max_tasks must be positive when supplied")
    return {
        "schema": AUTH_SCHEMA,
        "status": APPROVED_STATUS,
        "authorization_id": clean_id,
        "algorithm_version": ALGORITHM_VERSION,
        "stage": stage,
        "splits": clean_splits,
        "corpus_sha256": corpus_hash,
        "predictions_sha256": predictions_hash,
        "approval_token_sha256": _sha256_text(clean_token),
        "not_before": not_before,
        "expires_at": expires_at,
        "allow_sealed": bool(allow_sealed),
        "max_tasks": None if max_tasks is None else int(max_tasks),
        "issued_by": issued_by,
        "automatic_issuance_allowed": False,
    }


def _generic_validation(data: dict, *, token: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    if data.get("schema") != AUTH_SCHEMA:
        raise AuthorizationError(f"Authorization schema must be {AUTH_SCHEMA!r}")
    if data.get("status") != APPROVED_STATUS:
        raise AuthorizationError("DDS authorization is not in approved state")
    if data.get("algorithm_version") != ALGORITHM_VERSION:
        raise AuthorizationError(
            f"Authorization algorithm {data.get('algorithm_version')!r} does not match {ALGORITHM_VERSION!r}"
        )
    authorization_id = str(data.get("authorization_id", "")).strip()
    if not authorization_id:
        raise AuthorizationError("authorization_id is required")
    if data.get("automatic_issuance_allowed") is not False:
        raise AuthorizationError("Authorization must explicitly forbid automatic issuance")
    expected_token_hash = str(data.get("approval_token_sha256", "")).lower()
    actual_token_hash = _sha256_text(token.strip())
    if len(expected_token_hash) != 64 or not hmac.compare_digest(expected_token_hash, actual_token_hash):
        raise AuthorizationError("DDS approval token does not match the authorization")
    not_before = _parse_utc(data.get("not_before"), field="not_before")
    expires_at = _parse_utc(data.get("expires_at"), field="expires_at")
    if expires_at <= not_before:
        raise AuthorizationError("Authorization expires_at must be after not_before")
    current = (now or _now_utc()).astimezone(timezone.utc)
    if current < not_before:
        raise AuthorizationError("DDS authorization is not active yet")
    if current >= expires_at:
        raise AuthorizationError("DDS authorization has expired")
    return not_before, expires_at


def validate_authorization(
    *,
    path: Path,
    token: str,
    stage: str,
    splits: Iterable[str],
    work: Path,
    predictions: Path,
    requested_tasks: int | None,
    open_sealed: bool,
    now: datetime | None = None,
) -> AuthorizationContext:
    data = load_authorization(path)
    _, expires_at = _generic_validation(data, token=token, now=now)
    expected_splits = tuple(sorted({str(value) for value in splits}))
    manifest_splits = tuple(sorted(str(value) for value in data.get("splits", [])))
    if data.get("stage") != stage:
        raise AuthorizationError(f"Authorization stage {data.get('stage')!r} does not match {stage!r}")
    if manifest_splits != expected_splits:
        raise AuthorizationError(
            f"Authorization split set {manifest_splits!r} does not match requested {expected_splits!r}"
        )
    actual_corpus = corpus_sha256(work)
    if data.get("corpus_sha256") != actual_corpus:
        raise AuthorizationError("Authorization corpus SHA-256 does not match the prepared work directory")
    actual_predictions = sha256_file(predictions)
    if data.get("predictions_sha256") != actual_predictions:
        raise AuthorizationError("Authorization prediction SHA-256 does not match the locked prediction file")
    allow_sealed = bool(data.get("allow_sealed", False))
    if open_sealed and not allow_sealed:
        raise AuthorizationError("Authorization does not permit opening sealed_test")
    if "sealed_test" in expected_splits and not allow_sealed:
        raise AuthorizationError("Authorization does not permit sealed_test")
    max_tasks_raw = data.get("max_tasks")
    max_tasks = None if max_tasks_raw is None else int(max_tasks_raw)
    if max_tasks is not None:
        if max_tasks <= 0:
            raise AuthorizationError("Authorization max_tasks must be positive")
        if requested_tasks is None:
            raise AuthorizationError("Requested task count is required for a bounded authorization")
        if int(requested_tasks) > max_tasks:
            raise AuthorizationError(
                f"Requested {requested_tasks} tasks exceeds authorization maximum {max_tasks}"
            )
    context_sha = hashlib.sha256(_canonical_json(data) + b"\0" + token.strip().encode("utf-8")).hexdigest()
    return AuthorizationContext(
        authorization_id=str(data["authorization_id"]),
        context_sha256=context_sha,
        stage=stage,
        splits=expected_splits,
        corpus_sha256=actual_corpus,
        predictions_sha256=actual_predictions,
        expires_at=expires_at.isoformat().replace("+00:00", "Z"),
        allow_sealed=allow_sealed,
        max_tasks=max_tasks,
    )


def validate_engine_environment(*, now: datetime | None = None) -> dict:
    """Validate the authorization proof before a training DDS call.

    The training runner already requires ``DDS_TRAINING_CONFIRM=YES``.  Whenever
    that flag is present, solver entrypoints require a separately supplied,
    expiring authorization file, token and wrapper-derived context digest.
    """
    if os.environ.get("DDS_TEST_MODE") == "1" or os.environ.get("DDS_PREFLIGHT_MODE") == "1":
        return {"mode": "technical_test", "authorized": True}
    if os.environ.get("DDS_TRAINING_CONFIRM") != CONFIRM_TOKEN:
        return {"mode": "ordinary_local_solver_use", "authorized": True}
    path_text = os.environ.get("DDS_RUN_AUTH_FILE", "").strip()
    token = os.environ.get("DDS_RUN_APPROVAL_TOKEN", "").strip()
    claimed_context = os.environ.get("DDS_RUN_AUTH_CONTEXT", "").strip().lower()
    if not path_text or not token or len(claimed_context) != 64:
        raise AuthorizationError(
            "Mass DDS call blocked: use authorized_run_stage.py with an expiring authorization file and token"
        )
    path = Path(path_text)
    data = load_authorization(path)
    _generic_validation(data, token=token, now=now)
    actual_context = hashlib.sha256(_canonical_json(data) + b"\0" + token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_context, claimed_context):
        raise AuthorizationError("Mass DDS call blocked: authorization context digest is invalid")
    return {
        "mode": "mass_evaluation",
        "authorized": True,
        "authorization_id": str(data["authorization_id"]),
        "context_sha256": actual_context,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Verify an explicit DDS mass-run authorization; never starts DDS")
    p.add_argument("--authorization", required=True)
    p.add_argument("--approval-token", default=None)
    p.add_argument("--stage", required=True)
    p.add_argument("--splits", nargs="+", required=True)
    p.add_argument("--work", required=True)
    p.add_argument("--predictions", required=True)
    p.add_argument("--requested-tasks", type=int)
    p.add_argument("--open-sealed", action="store_true")
    args = p.parse_args()
    token = args.approval_token or os.environ.get("DDS_RUN_APPROVAL_TOKEN", "")
    context = validate_authorization(
        path=Path(args.authorization),
        token=token,
        stage=args.stage,
        splits=args.splits,
        work=Path(args.work),
        predictions=Path(args.predictions),
        requested_tasks=args.requested_tasks,
        open_sealed=args.open_sealed,
    )
    print(json.dumps({**context.__dict__, "authorization_valid": True, "dds_called": False}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
