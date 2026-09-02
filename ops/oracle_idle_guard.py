#!/usr/bin/env python3
"""Fail-closed Oracle idle classifier.

The classifier is deliberately pure: collectors provide a bounded telemetry
snapshot, and this module decides only BUSY / IDLE / UNKNOWN. STOP is never
performed here.

Rules:
* missing required telemetry => UNKNOWN
* stale telemetry => UNKNOWN
* contradictory telemetry => UNKNOWN
* malformed telemetry => UNKNOWN
* after the snapshot is proven complete/fresh/consistent, any BUSY source => BUSY
* only a complete set of explicit IDLE sources => IDLE
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STATES = frozenset({"BUSY", "IDLE", "UNKNOWN"})
REQUIRED_FAMILIES = (
    "assistant_lab_job",
    "assistant_lab_control_command",
    "assistant_lab_research_job",
    "assistant_lab_research_children",
    "assistant_lab_resident",
    "universal_video_neon",
    "universal_video_spool",
    "universal_video_resident",
    "ben",
    "bulk",
    "other_allowed_workloads",
    "observer_external_processes",
    "operator_maintenance_lease",
)


@dataclass(frozen=True)
class Verdict:
    state: str
    reason: str

    @property
    def stop_allowed(self) -> bool:
        return self.state == "IDLE"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "oracle-idle-verdict-v1",
            "state": self.state,
            "reason": self.reason,
            "stop_allowed": self.stop_allowed,
        }


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        return parsed.timestamp()
    raise ValueError("invalid timestamp")


def _family_status(entry: Mapping[str, Any]) -> tuple[str, str | None]:
    state = str(entry.get("state") or "").upper()
    if state not in STATES:
        return "UNKNOWN", "invalid_state"
    signals = entry.get("signals")
    if signals is not None:
        if not isinstance(signals, list) or not signals:
            return "UNKNOWN", "invalid_signals"
        seen: set[str] = set()
        for signal in signals:
            if not isinstance(signal, Mapping):
                return "UNKNOWN", "invalid_signal"
            signal_state = str(signal.get("state") or "").upper()
            if signal_state not in {"BUSY", "IDLE"}:
                return "UNKNOWN", "invalid_signal_state"
            seen.add(signal_state)
        if len(seen) > 1:
            return "UNKNOWN", "conflicting_telemetry"
        if state in {"BUSY", "IDLE"} and seen != {state}:
            return "UNKNOWN", "conflicting_telemetry"
    if entry.get("conflict") is True:
        return "UNKNOWN", "conflicting_telemetry"
    return state, None


def classify(snapshot: Mapping[str, Any], *, now: float | None = None) -> Verdict:
    if snapshot.get("schema") != "oracle-idle-telemetry-v1":
        return Verdict("UNKNOWN", "snapshot_schema_invalid")
    try:
        max_age = int(snapshot.get("max_age_seconds"))
        generated = _epoch(snapshot.get("generated_at"))
    except (TypeError, ValueError, OverflowError):
        return Verdict("UNKNOWN", "snapshot_time_invalid")
    if max_age < 1 or max_age > 900:
        return Verdict("UNKNOWN", "snapshot_max_age_invalid")
    current = float(now if now is not None else datetime.now(timezone.utc).timestamp())
    if generated > current + 5:
        return Verdict("UNKNOWN", "snapshot_from_future")
    if current - generated > max_age:
        return Verdict("UNKNOWN", "snapshot_stale")

    families = snapshot.get("families")
    if not isinstance(families, Mapping):
        return Verdict("UNKNOWN", "families_missing")
    missing = [name for name in REQUIRED_FAMILIES if name not in families]
    if missing:
        return Verdict("UNKNOWN", f"missing_telemetry:{missing[0]}")
    unexpected = sorted(set(families) - set(REQUIRED_FAMILIES))
    if unexpected:
        return Verdict("UNKNOWN", f"unexpected_telemetry:{unexpected[0]}")

    normalized: dict[str, str] = {}
    for name in REQUIRED_FAMILIES:
        entry = families[name]
        if not isinstance(entry, Mapping):
            return Verdict("UNKNOWN", f"invalid_telemetry:{name}")
        try:
            observed = _epoch(entry.get("observed_at"))
        except (TypeError, ValueError, OverflowError):
            return Verdict("UNKNOWN", f"telemetry_time_invalid:{name}")
        if observed > current + 5:
            return Verdict("UNKNOWN", f"telemetry_from_future:{name}")
        if current - observed > max_age:
            return Verdict("UNKNOWN", f"stale_telemetry:{name}")
        state, error = _family_status(entry)
        if error:
            return Verdict("UNKNOWN", f"{error}:{name}")
        if state == "UNKNOWN":
            return Verdict("UNKNOWN", f"source_unknown:{name}")
        normalized[name] = state

    busy = [name for name in REQUIRED_FAMILIES if normalized[name] == "BUSY"]
    if busy:
        return Verdict("BUSY", f"busy:{busy[0]}")
    return Verdict("IDLE", "all_required_telemetry_fresh_consistent_idle")


def bounded_evidence(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return secret-free evidence suitable for the guard command output."""
    families = snapshot.get("families")
    evidence: dict[str, Any] = {
        "telemetry_schema": snapshot.get("schema"),
        "generated_at": snapshot.get("generated_at"),
        "max_age_seconds": snapshot.get("max_age_seconds"),
        "families": {},
    }
    if isinstance(families, Mapping):
        safe: dict[str, Any] = {}
        for name in REQUIRED_FAMILIES:
            entry = families.get(name)
            if isinstance(entry, Mapping):
                safe[name] = {
                    "state": entry.get("state"),
                    "observed_at": entry.get("observed_at"),
                    "conflict": bool(entry.get("conflict") is True),
                }
            else:
                safe[name] = {"state": "MISSING", "observed_at": None, "conflict": False}
        evidence["families"] = safe
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    args = parser.parse_args()
    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(args.snapshot.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("snapshot is not an object")
        payload = loaded
        verdict = classify(payload)
    except Exception:
        verdict = Verdict("UNKNOWN", "snapshot_read_failed")
    result = verdict.as_dict()
    result["evidence"] = bounded_evidence(payload)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    print(f"ORACLE_IDLE_REASON={verdict.reason}")
    print(f"ORACLE_IDLE_STATE={verdict.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
