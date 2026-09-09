"""Portable Oracle/IBM runner for the frozen Bridgit recognizer holdout.

The external package uses only relative paths.  This runner materializes the
existing SHADOW_ONLY job locally, invokes the unchanged recognizer, and keeps
runtime measurements separate from the deterministic recognition payload.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import resource
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from bridge_vision.bridgit_rank_layout import (
    BACKEND_VERSION,
    JOB_TYPE,
    BridgitRankLayoutError,
    canonical_hash,
    execute_shadow_job,
)

RUNNER_INPUT_SCHEMA = "bridge-vision-bridgit-holdout-v1"
RUNNER_OUTPUT_SCHEMA = "bridge-vision-bridgit-holdout-run/v1"
RUNNER_VERSION = "bridgit-holdout-runner-v1"
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_CASES = 128

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_ALGORITHM_FILES = (
    "bridge_vision/anchor_registration.py",
    "bridge_vision/bridgit_rank_layout.py",
    "bridge_vision/deal_evidence.py",
    "bridge_contracts/video_deal.py",
)


class HoldoutRunnerError(ValueError):
    """The portable holdout package is malformed or not baseline-bound."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def recognizer_artifact_sha256() -> str:
    """Hash the exact algorithm source set, independent of checkout location."""
    repository_root = Path(__file__).resolve().parent.parent
    file_hashes = {
        relative: _sha256_bytes((repository_root / relative).read_bytes())
        for relative in _ALGORITHM_FILES
    }
    payload = json.dumps(
        file_hashes, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _load_package(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    if len(payload) > MAX_PACKAGE_BYTES:
        raise HoldoutRunnerError("holdout package exceeds size limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HoldoutRunnerError("holdout package is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise HoldoutRunnerError("holdout package must be an object")
    return dict(value)


def _string(raw: Any, field: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise HoldoutRunnerError(f"invalid {field}")
    return raw


def _portable_ref(
    raw: Any,
    field: str,
    package_root: Path,
    *,
    timestamp_required: bool = False,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise HoldoutRunnerError(f"{field} must be an object")
    declared = Path(_string(raw.get("path"), f"{field}.path"))
    if declared.is_absolute():
        raise HoldoutRunnerError(f"{field}.path must be relative")
    try:
        resolved = (package_root / declared).resolve(strict=True)
        resolved.relative_to(package_root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HoldoutRunnerError(f"{field}.path escapes package root") from exc
    if not resolved.is_file():
        raise HoldoutRunnerError(f"{field}.path must identify a file")
    sha = raw.get("sha256")
    if not isinstance(sha, str) or _HEX64.fullmatch(sha) is None:
        raise HoldoutRunnerError(f"invalid {field}.sha256")
    result: dict[str, Any] = {"path": str(resolved), "sha256": sha}
    if timestamp_required:
        timestamp = raw.get("timestamp_ms")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise HoldoutRunnerError(f"invalid {field}.timestamp_ms")
        result["timestamp_ms"] = timestamp
    return result


def _materialize_job(raw_case: Any, package_root: Path) -> tuple[str, dict[str, Any]]:
    if not isinstance(raw_case, Mapping):
        raise HoldoutRunnerError("case must be an object")
    case_id = _string(raw_case.get("case_id"), "case_id")
    profile_id = _string(raw_case.get("profile_id"), f"{case_id}.profile_id")
    frame_refs = raw_case.get("frame_refs")
    if (
        not isinstance(frame_refs, Sequence)
        or isinstance(frame_refs, (str, bytes))
        or not 1 <= len(frame_refs) <= 16
    ):
        raise HoldoutRunnerError(f"{case_id}.frame_refs count outside allowed range")
    pointer_events = raw_case.get("teacher_pointer_events", [])
    if not isinstance(pointer_events, Sequence) or isinstance(pointer_events, (str, bytes)):
        raise HoldoutRunnerError(f"{case_id}.teacher_pointer_events must be an array")
    job = {
        "job_type": JOB_TYPE,
        "production_write": False,
        "allow_hidden_information": False,
        "input_root": str(package_root),
        "profile_id": profile_id,
        "profile_ref": _portable_ref(raw_case.get("profile_ref"), f"{case_id}.profile_ref", package_root),
        "reference_frame_ref": _portable_ref(
            raw_case.get("reference_frame_ref"),
            f"{case_id}.reference_frame_ref",
            package_root,
        ),
        "frame_refs": [
            _portable_ref(
                item,
                f"{case_id}.frame_refs[{index}]",
                package_root,
                timestamp_required=True,
            )
            for index, item in enumerate(frame_refs)
        ],
        "teacher_pointer_events": list(pointer_events),
    }
    return case_id, job


def _directory_bytes(root: Path) -> int:
    total = 0
    try:
        for path in root.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                continue
    except OSError:
        return total
    return total


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if os.uname().sysname == "Darwin" else value * 1024


def _measure(call: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="bridgit-holdout-") as temporary:
        temp_root = Path(temporary)
        stop = threading.Event()
        peak_temp = [0]

        def sample() -> None:
            while not stop.wait(0.01):
                peak_temp[0] = max(peak_temp[0], _directory_bytes(temp_root))

        monitor = threading.Thread(target=sample, name="bridgit-holdout-temp-meter", daemon=True)
        old_tmpdir = os.environ.get("TMPDIR")
        old_tempdir = tempfile.tempdir
        os.environ["TMPDIR"] = str(temp_root)
        tempfile.tempdir = str(temp_root)
        wall_start = time.perf_counter()
        cpu_start = time.process_time()
        monitor.start()
        try:
            result = call()
        finally:
            wall_seconds = time.perf_counter() - wall_start
            cpu_seconds = time.process_time() - cpu_start
            stop.set()
            monitor.join(timeout=1.0)
            peak_temp[0] = max(peak_temp[0], _directory_bytes(temp_root))
            tempfile.tempdir = old_tempdir
            if old_tmpdir is None:
                os.environ.pop("TMPDIR", None)
            else:
                os.environ["TMPDIR"] = old_tmpdir
        metrics = {
            "wall_seconds": round(wall_seconds, 6),
            "cpu_seconds": round(cpu_seconds, 6),
            "cpu_utilization_ratio": round(cpu_seconds / wall_seconds, 6) if wall_seconds else 0.0,
            "peak_rss_bytes": _peak_rss_bytes(),
            "peak_temp_disk_bytes": peak_temp[0],
        }
        return result, metrics


def _portable_card_records(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    report = result.get("deal_evidence_report")
    if not isinstance(report, Mapping):
        raise HoldoutRunnerError("recognizer result has no deal_evidence_report")
    records = report.get("card_records")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise HoldoutRunnerError("deal_evidence_report.card_records is invalid")
    portable = []
    known_cards = []
    for raw in records:
        if not isinstance(raw, Mapping):
            raise HoldoutRunnerError("card record is invalid")
        provenance = str(raw.get("source") or "")
        unknown = provenance == "UNKNOWN"
        suit = raw.get("suit")
        rank = raw.get("rank")
        if not unknown and isinstance(suit, str) and isinstance(rank, str):
            known_cards.append(rank + suit)
        portable.append(
            {
                "seat": raw.get("seat"),
                "suit": suit,
                "rank": rank,
                "confidence": raw.get("confidence"),
                "provenance": provenance,
                "frame_sha256": raw.get("frame_sha256"),
                "recognizer_version": raw.get("recognizer_version"),
                "UNKNOWN": unknown,
            }
        )
    unknown_count = sum(bool(item["UNKNOWN"]) for item in portable)
    duplicate_card_count = len(known_cards) - len(set(known_cards))
    return portable, unknown_count, duplicate_card_count


def run_package(package_path: Path) -> dict[str, Any]:
    package_path = package_path.resolve(strict=True)
    package_root = package_path.parent.resolve(strict=True)
    package = _load_package(package_path)
    if package.get("schema") != RUNNER_INPUT_SCHEMA:
        raise HoldoutRunnerError("unsupported holdout package schema")
    head = package.get("recognizer_head_git_sha")
    if not isinstance(head, str) or _HEX40.fullmatch(head) is None:
        raise HoldoutRunnerError("invalid recognizer_head_git_sha")
    if package.get("recognizer_version") != BACKEND_VERSION:
        raise HoldoutRunnerError("recognizer version does not match frozen package")
    artifact_sha = recognizer_artifact_sha256()
    if package.get("recognizer_artifact_sha256") != artifact_sha:
        raise HoldoutRunnerError("recognizer artifact does not match frozen package")
    cases = package.get("cases")
    if (
        not isinstance(cases, Sequence)
        or isinstance(cases, (str, bytes))
        or not 1 <= len(cases) <= MAX_CASES
    ):
        raise HoldoutRunnerError("cases count outside allowed range")
    seen_case_ids: set[str] = set()
    case_outputs = []
    deterministic_case_hashes = []
    for raw_case in cases:
        case_id, job = _materialize_job(raw_case, package_root)
        if case_id in seen_case_ids:
            raise HoldoutRunnerError("duplicate case_id")
        seen_case_ids.add(case_id)
        receipt, runtime_metrics = _measure(lambda job=job: execute_shadow_job(job))
        result = receipt.get("result")
        if not isinstance(result, Mapping):
            raise HoldoutRunnerError("recognizer receipt has no result")
        records, unknown_count, duplicate_card_count = _portable_card_records(result)
        evidence_report = result.get("deal_evidence_report")
        stable = {
            "case_id": case_id,
            "recognizer_status": result.get("status"),
            "evidence_status": evidence_report.get("status") if isinstance(evidence_report, Mapping) else None,
            "recognizer_version": BACKEND_VERSION,
            "card_records": records,
            "UNKNOWN_count": unknown_count,
            "duplicate_card_count": duplicate_card_count,
            "recognizer_result_sha256": canonical_hash(result),
        }
        stable["portable_case_output_sha256"] = canonical_hash(stable)
        deterministic_case_hashes.append(stable["portable_case_output_sha256"])
        case_outputs.append({**stable, "recognizer_result": dict(result), "runtime_metrics": runtime_metrics})
    aggregate = {
        "wall_seconds": round(sum(item["runtime_metrics"]["wall_seconds"] for item in case_outputs), 6),
        "cpu_seconds": round(sum(item["runtime_metrics"]["cpu_seconds"] for item in case_outputs), 6),
        "peak_rss_bytes": max(item["runtime_metrics"]["peak_rss_bytes"] for item in case_outputs),
        "peak_temp_disk_bytes": max(item["runtime_metrics"]["peak_temp_disk_bytes"] for item in case_outputs),
    }
    portable_input_sha = canonical_hash(package)
    deterministic_receipt = {
        "recognizer_head_git_sha": head,
        "recognizer_artifact_sha256": artifact_sha,
        "recognizer_version": BACKEND_VERSION,
        "portable_input_sha256": portable_input_sha,
        "case_output_sha256s": deterministic_case_hashes,
    }
    return {
        "schema": RUNNER_OUTPUT_SCHEMA,
        "runner_version": RUNNER_VERSION,
        **deterministic_receipt,
        "deterministic_run_sha256": canonical_hash(deterministic_receipt),
        "cases": case_outputs,
        "runtime_metrics": aggregate,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run frozen Bridgit holdout cases portably")
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = run_package(args.package)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (HoldoutRunnerError, BridgitRankLayoutError, OSError, KeyError, MemoryError) as exc:
        print(f"RECOGNIZER_HOLDOUT_V1_REJECTED: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
