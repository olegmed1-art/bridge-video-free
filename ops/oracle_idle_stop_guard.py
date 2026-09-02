#!/usr/bin/env python3
"""Fail-closed authorizer for an Oracle idle-state proof.

This program never stops an instance. It emits YES only for one exact, fresh,
bounded-duration IDLE proof. Every BUSY, UNKNOWN, malformed, missing, stale,
future-dated, partial, or extra-output proof emits NO and exits non-zero.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

CONTRACT_VERSION = "2"
MAX_PROOF_BYTES = 4096
REASON_RE = re.compile(r"[A-Za-z0-9_./,:=+-]{1,1024}\Z")
EXPECTED_KEYS = (
    "ORACLE_IDLE_CONTRACT_VERSION",
    "ORACLE_IDLE_STARTED_AT_EPOCH",
    "ORACLE_IDLE_OBSERVED_AT_EPOCH",
    "ORACLE_IDLE_REASON",
    "ORACLE_IDLE_STATE",
)


@dataclass(frozen=True)
class Proof:
    started_at_epoch: int
    observed_at_epoch: int
    reason: str
    state: str


class ProofError(ValueError):
    """A stable, non-secret reason why STOP is forbidden."""


def _read_exact_lines(path: Path) -> list[str]:
    if path.is_symlink():
        raise ProofError("proof_symlink_forbidden")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProofError("proof_missing_or_unreadable") from exc
    if not raw or len(raw) > MAX_PROOF_BYTES:
        raise ProofError("proof_size_invalid")
    if b"\x00" in raw or b"\r" in raw:
        raise ProofError("proof_encoding_invalid")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ProofError("proof_encoding_invalid") from exc
    lines = text.splitlines()
    if text != "\n".join(lines) + "\n":
        raise ProofError("proof_framing_invalid")
    if len(lines) != len(EXPECTED_KEYS):
        raise ProofError("proof_line_count_invalid")
    return lines


def parse_proof(path: Path) -> Proof:
    lines = _read_exact_lines(path)
    values: dict[str, str] = {}
    for expected_key, line in zip(EXPECTED_KEYS, lines, strict=True):
        key, separator, value = line.partition("=")
        if separator != "=" or key != expected_key or not value:
            raise ProofError("proof_contract_invalid")
        values[key] = value

    if values["ORACLE_IDLE_CONTRACT_VERSION"] != CONTRACT_VERSION:
        raise ProofError("proof_version_invalid")

    try:
        started = int(values["ORACLE_IDLE_STARTED_AT_EPOCH"])
        observed = int(values["ORACLE_IDLE_OBSERVED_AT_EPOCH"])
    except ValueError as exc:
        raise ProofError("proof_timestamp_invalid") from exc
    if started <= 0 or observed <= 0 or observed < started:
        raise ProofError("proof_timestamp_invalid")

    reason = values["ORACLE_IDLE_REASON"]
    if REASON_RE.fullmatch(reason) is None:
        raise ProofError("proof_reason_invalid")

    state = values["ORACLE_IDLE_STATE"]
    if state not in {"BUSY", "IDLE", "UNKNOWN"}:
        raise ProofError("proof_state_invalid")

    return Proof(
        started_at_epoch=started,
        observed_at_epoch=observed,
        reason=reason,
        state=state,
    )


def authorize(
    proof: Proof,
    *,
    now_epoch: int,
    max_age_seconds: int,
    max_duration_seconds: int,
    max_future_skew_seconds: int,
) -> None:
    if not 1 <= max_age_seconds <= 300:
        raise ProofError("policy_max_age_invalid")
    if not 1 <= max_duration_seconds <= 300:
        raise ProofError("policy_max_duration_invalid")
    if not 0 <= max_future_skew_seconds <= 30:
        raise ProofError("policy_future_skew_invalid")

    duration = proof.observed_at_epoch - proof.started_at_epoch
    if duration > max_duration_seconds:
        raise ProofError("proof_duration_exceeded")
    if now_epoch - proof.observed_at_epoch > max_age_seconds:
        raise ProofError("proof_stale")
    if proof.observed_at_epoch - now_epoch > max_future_skew_seconds:
        raise ProofError("proof_from_future")
    if proof.state != "IDLE":
        raise ProofError(f"state_{proof.state.lower()}_forbids_stop")


def _emit(authorized: bool, reason: str) -> int:
    print(f"ORACLE_STOP_AUTHORIZED={'YES' if authorized else 'NO'}")
    print(f"ORACLE_STOP_AUTHORIZATION_REASON={reason}")
    return 0 if authorized else 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=int, default=30)
    parser.add_argument("--max-duration-seconds", type=int, default=30)
    parser.add_argument("--max-future-skew-seconds", type=int, default=5)
    args = parser.parse_args(argv)

    try:
        proof = parse_proof(args.proof)
        authorize(
            proof,
            now_epoch=int(time.time()),
            max_age_seconds=args.max_age_seconds,
            max_duration_seconds=args.max_duration_seconds,
            max_future_skew_seconds=args.max_future_skew_seconds,
        )
    except ProofError as exc:
        return _emit(False, str(exc))
    except Exception:
        # No unexpected exception may fail open or expose raw input.
        return _emit(False, "internal_authorizer_error")
    return _emit(True, "fresh_exact_idle")


if __name__ == "__main__":
    sys.exit(main())
