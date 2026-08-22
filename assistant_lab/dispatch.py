"""Pure capability helpers for bounded Assistant Lab dispatch."""
from __future__ import annotations

import hashlib
import secrets

from .contract import LabContractError

MIN_NONCE_LENGTH = 48
MAX_NONCE_LENGTH = 128


def dispatch_nonce_sha256(nonce: str) -> str:
    value = str(nonce or "").strip()
    if not (MIN_NONCE_LENGTH <= len(value) <= MAX_NONCE_LENGTH):
        raise LabContractError("invalid assistant-lab dispatch capability")
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise LabContractError("invalid assistant-lab dispatch capability") from exc
    return hashlib.sha256(encoded).hexdigest()


def verify_dispatch_nonce(stored_sha256: str | None, nonce: str) -> bool:
    if not stored_sha256:
        return False
    try:
        candidate = dispatch_nonce_sha256(nonce)
    except LabContractError:
        return False
    return secrets.compare_digest(str(stored_sha256), candidate)


__all__ = ["dispatch_nonce_sha256", "verify_dispatch_nonce"]
