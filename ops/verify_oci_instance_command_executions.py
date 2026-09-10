#!/usr/bin/env python3
"""Fail closed unless every OCI Run Command execution is exact and terminal."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any


INSTANCE_RE = re.compile(r"ocid1\.instance\.[A-Za-z0-9._-]+")
COMMAND_RE = re.compile(r"ocid1\.instanceagentcommand\.[A-Za-z0-9._-]+")
TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELED", "TIMED_OUT"})
KNOWN_STATES = TERMINAL_STATES | {"ACCEPTED", "IN_PROGRESS"}
MAX_INPUT_BYTES = 8_000_000


class CommandExecutionValidationError(ValueError):
    """The exact-instance execution inventory is incomplete or unsafe."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CommandExecutionValidationError(message)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        _require(key not in value, "duplicate OCI JSON key")
        value[key] = item
    return value


def _read_json(path: Path) -> Any:
    _require(path.is_file() and not path.is_symlink(), "unsafe OCI execution inventory")
    size = path.stat().st_size
    _require(0 < size <= MAX_INPUT_BYTES, "unsafe OCI execution inventory size")
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CommandExecutionValidationError("invalid OCI execution inventory") from exc


def validate_executions(payload: Any, expected_instance: str) -> int:
    """Return the unique execution count only when every execution is terminal."""

    _require(INSTANCE_RE.fullmatch(expected_instance) is not None, "invalid OCI instance identity")
    _require(type(payload) is dict and set(payload) >= {"data"}, "invalid OCI command execution list")
    items = payload["data"]
    _require(isinstance(items, list), "invalid OCI command execution collection")
    seen: set[str] = set()
    for item in items:
        _require(type(item) is dict, "invalid OCI command execution item")
        command_id = item.get("instance-agent-command-id")
        instance_id = item.get("instance-id")
        lifecycle = item.get("lifecycle-state")
        created = item.get("time-created")
        _require(
            isinstance(command_id, str) and COMMAND_RE.fullmatch(command_id) is not None,
            "invalid OCI Run Command identity",
        )
        _require(
            instance_id == expected_instance,
            "OCI Run Command execution belongs to another instance",
        )
        _require(
            isinstance(lifecycle, str) and lifecycle in KNOWN_STATES,
            "unknown OCI Run Command lifecycle",
        )
        _require(isinstance(created, str), "missing OCI Run Command timestamp")
        try:
            timestamp = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CommandExecutionValidationError(
                "invalid OCI Run Command timestamp"
            ) from exc
        _require(timestamp.tzinfo is not None, "timezone-free OCI Run Command timestamp")
        _require(command_id not in seen, "duplicate OCI Run Command execution identity")
        seen.add(command_id)
        _require(
            lifecycle in TERMINAL_STATES,
            f"OCI Run Command on target instance is not terminal: {lifecycle}",
        )
    return len(seen)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executions-json", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        count = validate_executions(_read_json(args.executions_json), args.instance_id)
        print(count)
        return 0
    except CommandExecutionValidationError as exc:
        print(f"OCI_INSTANCE_COMMAND_RECONCILIATION_FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
