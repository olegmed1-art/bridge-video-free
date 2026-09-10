"""Portable Oracle/IBM runner for the frozen Bridgit recognizer holdout.

The external package uses only relative paths. This runner materializes the
existing SHADOW_ONLY job locally, invokes the unchanged recognizer, and keeps
runtime measurements separate from the deterministic recognition payload.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import os
import re
import resource
import stat
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
from importlib import metadata
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
ALGORITHM_BASELINE_GIT_SHA = "3d8175efffe7c693451033b6bc11ff059a8e367d"
MAX_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_CASES = 128

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_BASELINE_BLOB_SHAS = {
    "bridge_vision/anchor_registration.py": "943552f8c6088b20005f9812820bf3773e58a764",
    "bridge_vision/bridgit_rank_layout.py": "242927cdbb088b9a0a02276ba8bd7e6e27f6ca98",
    "bridge_vision/deal_evidence.py": "d8b071816c916126d91e882b5e3ede1bdead252b",
    "bridge_contracts/video_deal.py": "7c0baf7edfdd15896f4379f61abecb555d32e9fb",
    "requirements-bridgit-rank-layout-shadow.txt": "7858f1d056d7708add5854ec38d12b18db43b4a8",
}
PINNED_RUNTIME_VERSIONS = {
    "numpy": "2.3.2",
    "opencv-python-headless": "5.0.0.93",
}
PINNED_RUNTIME_MODULES = {
    "numpy": ("numpy", "numpy/__init__.py"),
    "opencv-python-headless": ("cv2", "cv2/__init__.py"),
}
RUNTIME_NATIVE_RECORD_POLICY = "all-distribution-native-records-v1"


class HoldoutRunnerError(ValueError):
    """The portable holdout package is malformed or not baseline-bound."""


def _git_blob_sha(path: Path) -> str:
    try:
        size = path.stat().st_size
        digest = hashlib.sha1()
        digest.update(f"blob {size}\0".encode("ascii"))
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise HoldoutRunnerError("recognizer baseline artifact is unavailable") from exc


def _sha256_file(path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        raise HoldoutRunnerError("pixel runtime module is unavailable") from exc


def _current_baseline_blob_shas() -> dict[str, str]:
    repository_root = Path(__file__).resolve().parent.parent
    return {
        relative: _git_blob_sha(repository_root / relative)
        for relative in _BASELINE_BLOB_SHAS
    }


def _verify_algorithm_baseline() -> None:
    if _current_baseline_blob_shas() != _BASELINE_BLOB_SHAS:
        raise HoldoutRunnerError(
            "recognizer source does not match frozen algorithm baseline"
        )


def _record_sha256(record_hash: Any) -> str:
    if record_hash is None or getattr(record_hash, "mode", None) != "sha256":
        raise HoldoutRunnerError("pixel runtime module has no trusted RECORD hash")
    value = str(getattr(record_hash, "value", ""))
    try:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding).hex()
    except (ValueError, TypeError) as exc:
        raise HoldoutRunnerError("pixel runtime RECORD hash is invalid") from exc


def _is_native_runtime_path(relative: str) -> bool:
    name = Path(relative).name.lower()
    return any(marker in name for marker in (".so", ".pyd", ".dll", ".dylib"))


def _verified_record_file(distribution: Any, entry: Any, *, native: bool) -> Path:
    try:
        unresolved = Path(distribution.locate_file(entry))
        if unresolved.is_symlink():
            raise HoldoutRunnerError("pixel runtime RECORD file must not be a symlink")
        path = unresolved.resolve(strict=True)
    except HoldoutRunnerError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise HoldoutRunnerError("pixel runtime RECORD file is unavailable") from exc
    if not path.is_file():
        raise HoldoutRunnerError("pixel runtime RECORD file is not a regular file")
    if _sha256_file(path) != _record_sha256(getattr(entry, "hash", None)):
        kind = "native file" if native else "module file"
        raise HoldoutRunnerError(f"pixel runtime {kind} does not match RECORD")
    return path


def _verify_imported_runtime_modules() -> dict[str, dict[str, Any]]:
    """Verify entry modules and every native file shipped by the pinned wheels."""
    identities: dict[str, dict[str, Any]] = {}
    for distribution_name, (module_name, expected_relative) in PINNED_RUNTIME_MODULES.items():
        try:
            distribution = metadata.distribution(distribution_name)
        except metadata.PackageNotFoundError as exc:
            raise HoldoutRunnerError(
                f"required pixel runtime is not installed: {distribution_name}"
            ) from exc
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise HoldoutRunnerError(
                f"required pixel runtime module is unavailable: {module_name}"
            ) from exc
        raw_module_path = getattr(module, "__file__", None)
        if not isinstance(raw_module_path, str) or not raw_module_path:
            raise HoldoutRunnerError("pixel runtime module origin is unavailable")
        try:
            module_path = Path(raw_module_path).resolve(strict=True)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HoldoutRunnerError("pixel runtime module origin is unavailable") from exc
        if not module_path.is_file():
            raise HoldoutRunnerError("pixel runtime module origin is not a file")
        files = distribution.files
        if files is None:
            raise HoldoutRunnerError("pixel runtime distribution has no file manifest")
        matched = None
        native_entries = []
        for entry in files:
            relative = str(entry).replace("\\", "/")
            if _is_native_runtime_path(relative):
                native_entries.append((relative, entry))
            try:
                located = Path(distribution.locate_file(entry)).resolve(strict=True)
            except (OSError, ValueError, RuntimeError):
                continue
            if located == module_path:
                matched = entry
        if matched is None:
            raise HoldoutRunnerError(
                "imported pixel runtime module is not owned by frozen distribution"
            )
        relative = str(matched).replace("\\", "/")
        if relative != expected_relative:
            raise HoldoutRunnerError("pixel runtime module origin does not match baseline")
        _verified_record_file(distribution, matched, native=False)
        if not native_entries:
            raise HoldoutRunnerError("pixel runtime distribution has no native files")
        verified_native_paths = []
        for native_relative, native_entry in sorted(native_entries):
            _verified_record_file(distribution, native_entry, native=True)
            verified_native_paths.append(native_relative)
        identities[distribution_name] = {
            "entry_module": relative,
            "native_files": verified_native_paths,
        }
    return identities


def _installed_runtime_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in PINNED_RUNTIME_VERSIONS:
        try:
            versions[distribution] = metadata.version(distribution)
        except metadata.PackageNotFoundError as exc:
            raise HoldoutRunnerError(
                f"required pixel runtime is not installed: {distribution}"
            ) from exc
    _verify_imported_runtime_modules()
    return versions


def _verified_runtime_versions() -> dict[str, str]:
    versions = _installed_runtime_versions()
    if versions != PINNED_RUNTIME_VERSIONS:
        raise HoldoutRunnerError("pixel runtime versions do not match frozen baseline")
    return versions


def frozen_recognizer_artifact_sha256() -> str:
    """Return the trusted artifact identity encoded by the frozen baseline."""
    payload = {
        "algorithm_baseline_git_sha": ALGORITHM_BASELINE_GIT_SHA,
        "backend_version": BACKEND_VERSION,
        "git_blob_shas": _BASELINE_BLOB_SHAS,
        "runtime_versions": PINNED_RUNTIME_VERSIONS,
        "runtime_module_paths": {
            name: relative
            for name, (_, relative) in PINNED_RUNTIME_MODULES.items()
        },
        "runtime_native_record_policy": RUNTIME_NATIVE_RECORD_POLICY,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def recognizer_artifact_sha256() -> str:
    """Verify local source/runtime and return the trusted portable artifact identity."""
    _verify_algorithm_baseline()
    _verified_runtime_versions()
    return frozen_recognizer_artifact_sha256()


def _read_bounded_regular_file(path: Path, max_bytes: int, kind: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise HoldoutRunnerError(f"{kind} is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise HoldoutRunnerError(f"{kind} must be a regular file")
        if info.st_size > max_bytes:
            raise HoldoutRunnerError(f"{kind} exceeds size limit")
        chunks: list[bytes] = []
        remaining = max_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except HoldoutRunnerError:
        raise
    except OSError as exc:
        raise HoldoutRunnerError(f"{kind} is unavailable") from exc
    finally:
        os.close(descriptor)
    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise HoldoutRunnerError(f"{kind} exceeds size limit")
    return payload


def _load_package(path: Path) -> dict[str, Any]:
    payload = _read_bounded_regular_file(path, MAX_PACKAGE_BYTES, "holdout package")
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


def _resolve_portable_path(raw: Any, field: str, package_root: Path) -> Path:
    declared = Path(_string(raw, field))
    if declared.is_absolute():
        raise HoldoutRunnerError(f"{field} must be relative")
    try:
        resolved = (package_root / declared).resolve(strict=True)
        resolved.relative_to(package_root)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HoldoutRunnerError(f"{field} escapes package root") from exc
    if not resolved.is_file():
        raise HoldoutRunnerError(f"{field} must identify a file")
    return resolved


def _portable_ref(
    raw: Any,
    field: str,
    package_root: Path,
    *,
    timestamp_required: bool = False,
) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise HoldoutRunnerError(f"{field} must be an object")
    resolved = _resolve_portable_path(raw.get("path"), f"{field}.path", package_root)
    sha = raw.get("sha256")
    if not isinstance(sha, str) or _HEX64.fullmatch(sha) is None:
        raise HoldoutRunnerError(f"invalid {field}.sha256")
    result: dict[str, Any] = {"path": str(resolved), "sha256": sha}
    if timestamp_required:
        timestamp = raw.get("timestamp_ms")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp < 0
        ):
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
    if not isinstance(pointer_events, Sequence) or isinstance(
        pointer_events, (str, bytes)
    ):
        raise HoldoutRunnerError(
            f"{case_id}.teacher_pointer_events must be an array"
        )
    job = {
        "job_type": JOB_TYPE,
        "production_write": False,
        "allow_hidden_information": False,
        "input_root": str(package_root),
        "profile_id": profile_id,
        "profile_ref": _portable_ref(
            raw_case.get("profile_ref"), f"{case_id}.profile_ref", package_root
        ),
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

        monitor = threading.Thread(
            target=sample, name="bridgit-holdout-temp-meter", daemon=True
        )
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
            "cpu_utilization_ratio": (
                round(cpu_seconds / wall_seconds, 6) if wall_seconds else 0.0
            ),
            "peak_temp_disk_bytes": peak_temp[0],
        }
        return result, metrics


def _portable_card_records(
    result: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], int, int]:
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
    package = _load_package(package_path)
    try:
        package_path = package_path.resolve(strict=True)
        package_root = package_path.parent.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HoldoutRunnerError("holdout package is unavailable") from exc
    if package.get("schema") != RUNNER_INPUT_SCHEMA:
        raise HoldoutRunnerError("unsupported holdout package schema")
    if package.get("runner_version") != RUNNER_VERSION:
        raise HoldoutRunnerError("runner version does not match frozen package")
    head = package.get("recognizer_head_git_sha")
    if head != ALGORITHM_BASELINE_GIT_SHA:
        raise HoldoutRunnerError(
            "recognizer head does not match frozen algorithm baseline"
        )
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
        receipt, runtime_metrics = _measure(
            lambda job=job: execute_shadow_job(job)
        )
        result = receipt.get("result")
        if not isinstance(result, Mapping):
            raise HoldoutRunnerError("recognizer receipt has no result")
        records, unknown_count, duplicate_card_count = _portable_card_records(result)
        evidence_report = result.get("deal_evidence_report")
        stable = {
            "case_id": case_id,
            "recognizer_status": result.get("status"),
            "evidence_status": (
                evidence_report.get("status")
                if isinstance(evidence_report, Mapping)
                else None
            ),
            "recognizer_version": BACKEND_VERSION,
            "card_records": records,
            "UNKNOWN_count": unknown_count,
            "duplicate_card_count": duplicate_card_count,
            "recognizer_result_sha256": canonical_hash(result),
        }
        stable["portable_case_output_sha256"] = canonical_hash(stable)
        deterministic_case_hashes.append(stable["portable_case_output_sha256"])
        case_outputs.append(
            {
                **stable,
                "recognizer_result": dict(result),
                "runtime_metrics": runtime_metrics,
            }
        )
    aggregate = {
        "wall_seconds": round(
            sum(item["runtime_metrics"]["wall_seconds"] for item in case_outputs), 6
        ),
        "cpu_seconds": round(
            sum(item["runtime_metrics"]["cpu_seconds"] for item in case_outputs), 6
        ),
        "peak_rss_bytes": _peak_rss_bytes(),
        "peak_temp_disk_bytes": max(
            item["runtime_metrics"]["peak_temp_disk_bytes"] for item in case_outputs
        ),
    }
    portable_input_sha = canonical_hash(package)
    deterministic_receipt = {
        "recognizer_head_git_sha": head,
        "recognizer_artifact_sha256": artifact_sha,
        "recognizer_version": BACKEND_VERSION,
        "runner_version": RUNNER_VERSION,
        "portable_input_sha256": portable_input_sha,
        "case_output_sha256s": deterministic_case_hashes,
    }
    return {
        "schema": RUNNER_OUTPUT_SCHEMA,
        **deterministic_receipt,
        "deterministic_run_sha256": canonical_hash(deterministic_receipt),
        "cases": case_outputs,
        "runtime_metrics": aggregate,
    }


def _sealed_input_paths(package_path: Path, package: Mapping[str, Any]) -> list[Path]:
    try:
        resolved_package = package_path.resolve(strict=True)
        package_root = resolved_package.parent.resolve(strict=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HoldoutRunnerError("holdout package is unavailable") from exc
    candidates = [resolved_package]
    cases = package.get("cases")
    if not isinstance(cases, Sequence) or isinstance(cases, (str, bytes)):
        raise HoldoutRunnerError("cases must be an array")
    for case_index, raw_case in enumerate(cases):
        if not isinstance(raw_case, Mapping):
            raise HoldoutRunnerError(f"cases[{case_index}] must be an object")
        for field in ("profile_ref", "reference_frame_ref"):
            raw_ref = raw_case.get(field)
            if not isinstance(raw_ref, Mapping):
                raise HoldoutRunnerError(
                    f"cases[{case_index}].{field} must be an object"
                )
            candidates.append(
                _resolve_portable_path(
                    raw_ref.get("path"),
                    f"cases[{case_index}].{field}.path",
                    package_root,
                )
            )
        frame_refs = raw_case.get("frame_refs")
        if not isinstance(frame_refs, Sequence) or isinstance(
            frame_refs, (str, bytes)
        ):
            raise HoldoutRunnerError(
                f"cases[{case_index}].frame_refs must be an array"
            )
        for frame_index, raw_ref in enumerate(frame_refs):
            if not isinstance(raw_ref, Mapping):
                raise HoldoutRunnerError(
                    f"cases[{case_index}].frame_refs[{frame_index}] must be an object"
                )
            candidates.append(
                _resolve_portable_path(
                    raw_ref.get("path"),
                    f"cases[{case_index}].frame_refs[{frame_index}].path",
                    package_root,
                )
            )
    return candidates


def _validate_output_target(package_path: Path, output_path: Path) -> None:
    package = _load_package(package_path)
    candidates = _sealed_input_paths(package_path, package)
    try:
        resolved_output = output_path.resolve(strict=False)
        if resolved_output.is_dir():
            raise HoldoutRunnerError("output target must not be a directory")
        for candidate in candidates:
            if resolved_output == candidate:
                raise HoldoutRunnerError("output path aliases sealed holdout input")
            if (
                output_path.exists()
                and candidate.exists()
                and os.path.samefile(output_path, candidate)
            ):
                raise HoldoutRunnerError("output path aliases sealed holdout input")
    except HoldoutRunnerError:
        raise
    except OSError as exc:
        raise HoldoutRunnerError("output path cannot be validated") from exc


def _pin_output_directory(path: Path) -> int:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        parent = path.parent.resolve(strict=True)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(parent, flags)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise HoldoutRunnerError("output parent must be a directory")
        return descriptor
    except HoldoutRunnerError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise HoldoutRunnerError("output directory is unavailable") from exc


def _atomic_write_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    directory_descriptor: int | None = None,
) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    owns_descriptor = directory_descriptor is None
    if directory_descriptor is None:
        directory_descriptor = _pin_output_directory(path)
    temporary_name: str | None = None
    try:
        pinned_parent = Path(f"/proc/self/fd/{directory_descriptor}")
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=pinned_parent
        )
        temporary_name = Path(temporary_path).name
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        temporary_name = None
        os.fsync(directory_descriptor)
    except OSError as exc:
        raise HoldoutRunnerError("output write failed") from exc
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                pass
        if owns_descriptor:
            os.close(directory_descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run frozen Bridgit holdout cases portably"
    )
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    output_directory_descriptor: int | None = None
    try:
        _validate_output_target(args.package, args.output)
        output_directory_descriptor = _pin_output_directory(args.output)
        pinned_output = (
            Path(f"/proc/self/fd/{output_directory_descriptor}") / args.output.name
        )
        _validate_output_target(args.package, pinned_output)
        output = run_package(args.package)
        _validate_output_target(args.package, pinned_output)
        _atomic_write_json(
            args.output,
            output,
            directory_descriptor=output_directory_descriptor,
        )
    except (
        HoldoutRunnerError,
        BridgitRankLayoutError,
        OSError,
        KeyError,
        MemoryError,
    ) as exc:
        print(f"RECOGNIZER_HOLDOUT_V1_REJECTED: {exc}", file=os.sys.stderr)
        return 2
    finally:
        if output_directory_descriptor is not None:
            os.close(output_directory_descriptor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
