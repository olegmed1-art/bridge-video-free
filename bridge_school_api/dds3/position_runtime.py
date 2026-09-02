"""Persistent SolverContext-backed DDS3 position analysis boundary.

The C++ worker owns one SolverContext for its process lifetime. Python only validates
and normalizes DDS3 output; it never invents numerical bridge values.
"""
from __future__ import annotations

import atexit
import json
import os
import select
import subprocess
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from .position_contract import MoveValue, rank_move_values, trajectory


class PositionWorkerUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DDS3PositionConfig:
    executable: str = os.getenv("DDS3_POSITION_WORKER", "/opt/bridge-school-dds3/dds_position_worker")
    timeout_seconds: float = float(os.getenv("DDS3_POSITION_TIMEOUT_SECONDS", "20"))


def _safe_field(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text or any(ch in text for ch in "\t\r\n"):
        raise ValueError(f"invalid {name}")
    return text


def _position_line(position: Mapping[str, Any]) -> str:
    pbn = _safe_field(position.get("pbn"), "position pbn")
    trump = _safe_field(position.get("trump"), "position trump").upper()
    first = _safe_field(position.get("first"), "position first").upper()
    raw_current = position.get("current_trick") or []
    if not isinstance(raw_current, list) or len(raw_current) > 3:
        raise ValueError("current_trick must be a list of at most three cards")
    cards = [_safe_field(card, "current trick card").upper() for card in raw_current]
    current = ",".join(cards) if cards else "-"
    return "\t".join(("POSITION", trump, first, current, pbn))


class PositionWorker:
    def __init__(self, config: DDS3PositionConfig | None = None) -> None:
        self.config = config or DDS3PositionConfig()
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen[str]:
        try:
            proc = subprocess.Popen(
                [self.config.executable],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise PositionWorkerUnavailable("DDS3_POSITION_WORKER_UNAVAILABLE") from exc
        self._proc = proc
        return proc

    def _ensure(self) -> subprocess.Popen[str]:
        if self._proc is None or self._proc.poll() is not None:
            self.close()
            return self._start()
        return self._proc

    def call(self, position: Mapping[str, Any]) -> dict[str, Any]:
        line = _position_line(position)
        with self._lock:
            proc = self._ensure()
            if proc.stdin is None or proc.stdout is None:
                self.close()
                raise PositionWorkerUnavailable("DDS3_POSITION_WORKER_PIPE_UNAVAILABLE")
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                self.close()
                raise PositionWorkerUnavailable("DDS3_POSITION_WORKER_WRITE_FAILED") from exc
            ready, _, _ = select.select([proc.stdout], [], [], self.config.timeout_seconds)
            if not ready:
                self.close()
                raise PositionWorkerUnavailable("DDS3_POSITION_WORKER_TIMEOUT")
            raw = proc.stdout.readline()
            if not raw:
                self.close()
                raise PositionWorkerUnavailable("DDS3_POSITION_WORKER_EOF")
            try:
                result = json.loads(raw)
            except json.JSONDecodeError as exc:
                self.close()
                raise PositionWorkerUnavailable("DDS3_POSITION_WORKER_INVALID_JSON") from exc
            if result.get("engine") != "DDS3" or result.get("fallback_used") is not False:
                raise PositionWorkerUnavailable("DDS3_POSITION_PROVENANCE_INVALID")
            if result.get("ok") is not True:
                error = str(result.get("error") or "DDS3_POSITION_FAILED")
                if error.startswith("DDS_"):
                    raise PositionWorkerUnavailable(error)
                raise ValueError(error)
            return result

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1)


_DEFAULT_WORKER = PositionWorker()
atexit.register(_DEFAULT_WORKER.close)


def solve_position_all_moves(
    position: Mapping[str, Any], *, worker: PositionWorker | None = None
) -> dict[str, Any]:
    runtime = worker or _DEFAULT_WORKER
    raw = runtime.call(position)
    raw_moves = raw.get("moves")
    if not isinstance(raw_moves, list) or not raw_moves:
        raise PositionWorkerUnavailable("DDS3_POSITION_RETURNED_NO_MOVES")
    values = [
        MoveValue(str(item["card"]), int(item["tricks_for_side_to_play"]))
        for item in raw_moves
    ]
    ranked = rank_move_values(values)
    metadata = {str(item["card"]): item for item in raw_moves}
    for item in ranked["moves"]:
        source = metadata[item["card"]]
        item["equivalent"] = bool(source.get("equivalent"))
        item["representative"] = source.get("representative")
    return {
        "engine": "DDS3",
        "fallback_used": False,
        "operation": "position_all_moves",
        "tricks_semantics": "maximum tricks for side to play from this position",
        "best_tricks": ranked["best_tricks"],
        "optimal_cards": ranked["optimal_cards"],
        "moves": ranked["moves"],
        "tricks_remaining": int(raw["tricks_remaining"]),
        "nodes": int(raw.get("nodes") or 0),
        "solver_context": {
            "request_seq": int(raw.get("request_seq") or 0),
            "tt_present_before": bool(raw.get("tt_present_before")),
            "tt_present_after": bool(raw.get("tt_present_after")),
            "same_tt_instance": bool(raw.get("same_tt_instance")),
        },
    }


def solve_position_trajectory(
    positions: list[Mapping[str, Any]],
    *,
    perspective: str,
    worker: PositionWorker | None = None,
) -> dict[str, Any]:
    if not positions:
        raise ValueError("positions must contain at least V0")
    side = perspective.upper()
    if side not in {"NS", "EW"}:
        raise ValueError("perspective must be NS or EW")
    runtime = worker or _DEFAULT_WORKER
    values: list[int] = []
    samples: list[dict[str, Any]] = []
    for index, position in enumerate(positions):
        if "perspective_tricks_won" not in position:
            raise ValueError("each trajectory position requires perspective_tricks_won")
        won = int(position["perspective_tricks_won"])
        if won < 0 or won > 13:
            raise ValueError("perspective_tricks_won must be between 0 and 13")
        solved = solve_position_all_moves(position, worker=runtime)
        first = _safe_field(position.get("first"), "position first").upper()
        first_side = "NS" if first in {"N", "S"} else "EW"
        future = solved["best_tricks"] if first_side == side else solved["tricks_remaining"] - solved["best_tricks"]
        value = won + future
        values.append(value)
        samples.append(
            {
                "index": index,
                "value": value,
                "perspective_tricks_won": won,
                "future_tricks_for_perspective": future,
                "optimal_cards_for_side_to_play": solved["optimal_cards"],
                "nodes": solved["nodes"],
                "solver_context": solved["solver_context"],
            }
        )
    result = trajectory(values)
    result.update(
        {
            "operation": "position_trajectory",
            "perspective": side,
            "samples": samples,
            "engine": "DDS3",
            "fallback_used": False,
        }
    )
    return result


__all__ = [
    "DDS3PositionConfig",
    "PositionWorker",
    "PositionWorkerUnavailable",
    "solve_position_all_moves",
    "solve_position_trajectory",
]
