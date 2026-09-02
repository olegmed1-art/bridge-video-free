from __future__ import annotations

"""Manifest-driven isolated test runner for the DDS learning project.

The project self-tests intentionally use plain ``assert`` statements.  This
runner therefore refuses optimized Python (``python -O``), validates that all
self-test scripts and production modules are accounted for, executes every case
in an isolated subprocess with a timeout, terminates the whole subprocess group
on timeout, detects source-tree mutation, and writes one machine-readable
report instead of stopping at the first failure.
"""

import argparse
import hashlib
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

MANIFEST_SCHEMA = "dds-test-manifest-v1"
REPORT_SCHEMA = "dds-test-report-v1"
MAX_CAPTURE_CHARS = 200_000
ALLOWED_SUITES = {"fast", "dds"}
REQUIRED_CORE_TESTS = {
    "test-runner": ("test_runner_selftest.py", "fast"),
    "test-architecture": ("test_architecture_selftest.py", "fast"),
}
IGNORED_DIRS = {
    ".git",
    ".venv",
    ".build",
    ".wheel-cache",
    "__pycache__",
    "work",
    "checkpoints",
}
SOURCE_SUFFIXES = {".py", ".json", ".md", ".txt", ".sh", ".yml", ".yaml"}


class ManifestError(ValueError):
    """Raised when the test manifest is incomplete, ambiguous, or unsafe."""


@dataclass(frozen=True)
class TestSpec:
    test_id: str
    path: Path
    relative_path: str
    suite: str
    timeout_seconds: float
    hash_seeds: tuple[int, ...]
    description: str


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_relative_path(root: Path, value: object) -> tuple[Path, str]:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ManifestError("Test path is empty")
    candidate = Path(text)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ManifestError(f"Test path must stay inside the test root: {text!r}")
    resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ManifestError(f"Test path escapes the test root: {text!r}") from exc
    return resolved, relative


def _is_ignored(root: Path, path: Path) -> bool:
    try:
        parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in parts)


def _recursive_files(root: Path, pattern: str) -> list[Path]:
    return sorted(
        path.resolve()
        for path in root.rglob(pattern)
        if path.is_file() and not _is_ignored(root, path)
    )


def load_manifest(
    manifest_path: Path,
    *,
    root: Path | None = None,
    enforce_core: bool = True,
) -> tuple[dict, list[TestSpec]]:
    manifest_path = manifest_path.resolve()
    root = (root or manifest_path.parent).resolve()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Cannot read test manifest {manifest_path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
        raise ManifestError(f"Manifest schema must be {MANIFEST_SCHEMA!r}")
    rows = data.get("tests")
    if not isinstance(rows, list) or not rows:
        raise ManifestError("Manifest must contain a non-empty tests list")

    default_timeout = float(data.get("default_timeout_seconds", 60))
    default_hash_seeds = data.get("default_hash_seeds", [0])
    if default_timeout <= 0:
        raise ManifestError("default_timeout_seconds must be positive")
    if not isinstance(default_hash_seeds, list) or not default_hash_seeds:
        raise ManifestError("default_hash_seeds must be a non-empty list")

    ids: set[str] = set()
    paths: set[str] = set()
    specs: list[TestSpec] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ManifestError(f"tests[{index}] must be an object")
        test_id = str(row.get("id", "")).strip()
        suite = str(row.get("suite", "")).strip()
        if not test_id or not suite:
            raise ManifestError(f"tests[{index}] requires non-empty id and suite")
        if enforce_core and suite not in ALLOWED_SUITES:
            raise ManifestError(f"Unsupported test suite {suite!r} for {test_id}")
        if test_id in ids:
            raise ManifestError(f"Duplicate test id: {test_id}")
        path, relative = _safe_relative_path(root, row.get("path"))
        if relative in paths:
            raise ManifestError(f"Test path listed more than once: {relative}")
        if not path.is_file():
            raise ManifestError(f"Test file does not exist: {relative}")
        if path.suffix != ".py":
            raise ManifestError(f"Test entry must be a Python file: {relative}")
        timeout = float(row.get("timeout_seconds", default_timeout))
        if timeout <= 0:
            raise ManifestError(f"Timeout must be positive for {test_id}")
        raw_seeds = row.get("hash_seeds", default_hash_seeds)
        if not isinstance(raw_seeds, list) or not raw_seeds:
            raise ManifestError(f"hash_seeds must be a non-empty list for {test_id}")
        try:
            seeds = tuple(int(value) for value in raw_seeds)
        except (TypeError, ValueError) as exc:
            raise ManifestError(f"hash_seeds must contain integers for {test_id}") from exc
        if len(set(seeds)) != len(seeds):
            raise ManifestError(f"Duplicate hash seed for {test_id}: {seeds}")
        description = str(row.get("description", "")).strip()
        if enforce_core and not description:
            raise ManifestError(f"Test requires a non-empty description: {test_id}")
        ids.add(test_id)
        paths.add(relative)
        specs.append(
            TestSpec(
                test_id=test_id,
                path=path,
                relative_path=relative,
                suite=suite,
                timeout_seconds=timeout,
                hash_seeds=seeds,
                description=description,
            )
        )

    if enforce_core:
        by_id = {spec.test_id: spec for spec in specs}
        for test_id, (expected_path, expected_suite) in REQUIRED_CORE_TESTS.items():
            spec = by_id.get(test_id)
            if spec is None:
                raise ManifestError(f"Required core test is missing: {test_id}")
            if (spec.relative_path, spec.suite) != (expected_path, expected_suite):
                raise ManifestError(
                    f"Required core test {test_id} must be "
                    f"{expected_path!r} in suite {expected_suite!r}"
                )

    ignored = data.get("ignored_selftests", {})
    if not isinstance(ignored, dict):
        raise ManifestError("ignored_selftests must be an object mapping path to reason")
    ignored_paths: set[str] = set()
    for raw_path, raw_reason in ignored.items():
        ignored_path, relative = _safe_relative_path(root, raw_path)
        reason = str(raw_reason).strip()
        if not reason:
            raise ManifestError(f"Ignored self-test requires a reason: {relative}")
        if not ignored_path.is_file():
            raise ManifestError(f"Ignored self-test does not exist: {relative}")
        if relative in paths:
            raise ManifestError(f"Self-test cannot be both listed and ignored: {relative}")
        ignored_paths.add(relative)

    discovered = {
        path.relative_to(root).as_posix()
        for path in _recursive_files(root, "*_selftest.py")
    }
    orphaned = sorted(discovered - paths - ignored_paths)
    if orphaned:
        raise ManifestError(f"Unaccounted self-tests: {orphaned}")
    stale_ignored = sorted(ignored_paths - discovered)
    if stale_ignored:
        raise ManifestError(f"ignored_selftests contains non-selftest paths: {stale_ignored}")

    coverage = data.get("coverage")
    if coverage is None:
        raise ManifestError("Manifest must contain an explicit coverage contract")
    if not isinstance(coverage, dict):
        raise ManifestError("coverage must be an object")
    module_tests = coverage.get("module_tests", {})
    waivers = coverage.get("waivers", {})
    infrastructure = coverage.get("infrastructure", [])
    max_waivers = coverage.get("max_waivers")
    if not isinstance(module_tests, dict) or not isinstance(waivers, dict):
        raise ManifestError("coverage.module_tests and coverage.waivers must be objects")
    if not isinstance(infrastructure, list):
        raise ManifestError("coverage.infrastructure must be a list")
    if isinstance(max_waivers, bool) or not isinstance(max_waivers, int) or max_waivers < 0:
        raise ManifestError("coverage.max_waivers must be a non-negative integer")
    if len(waivers) > max_waivers:
        raise ManifestError(
            f"Coverage waiver budget exceeded: {len(waivers)} > {max_waivers}"
        )

    normalized_modules: dict[str, list[str]] = {}
    for raw_module, raw_test_ids in module_tests.items():
        module_path, relative = _safe_relative_path(root, raw_module)
        if not module_path.is_file():
            raise ManifestError(f"Covered module does not exist: {relative}")
        if not isinstance(raw_test_ids, list) or not raw_test_ids:
            raise ManifestError(f"Covered module requires at least one test id: {relative}")
        mapped = [str(value).strip() for value in raw_test_ids]
        unknown = sorted({value for value in mapped if value not in ids})
        if unknown:
            raise ManifestError(f"Covered module {relative} references unknown tests: {unknown}")
        normalized_modules[relative] = mapped

    normalized_waivers: dict[str, str] = {}
    for raw_module, raw_reason in waivers.items():
        module_path, relative = _safe_relative_path(root, raw_module)
        reason = str(raw_reason).strip()
        if not module_path.is_file():
            raise ManifestError(f"Waived module does not exist: {relative}")
        if not reason:
            raise ManifestError(f"Coverage waiver requires a reason: {relative}")
        normalized_waivers[relative] = reason

    normalized_infrastructure: set[str] = set()
    for raw_module in infrastructure:
        module_path, relative = _safe_relative_path(root, raw_module)
        if not module_path.is_file():
            raise ManifestError(f"Infrastructure module does not exist: {relative}")
        normalized_infrastructure.add(relative)

    overlap = sorted(set(normalized_modules) & set(normalized_waivers))
    if overlap:
        raise ManifestError(f"Modules cannot be both covered and waived: {overlap}")
    test_files = paths | ignored_paths
    production = {
        path.relative_to(root).as_posix()
        for path in _recursive_files(root, "*.py")
        if path.relative_to(root).as_posix() not in test_files
        and not path.name.endswith("_selftest.py")
    }
    accounted = set(normalized_modules) | set(normalized_waivers) | normalized_infrastructure
    missing_coverage = sorted(production - accounted)
    if missing_coverage:
        raise ManifestError(f"Production modules lack a test mapping or waiver: {missing_coverage}")
    stale_coverage = sorted(accounted - production)
    if stale_coverage:
        raise ManifestError(f"Coverage entries do not refer to production modules: {stale_coverage}")
    return data, specs


def _tree_snapshot(root: Path, *, extra_ignored: Iterable[Path] = ()) -> dict[str, str]:
    ignored_resolved = {path.resolve() for path in extra_ignored}
    snapshot: dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or _is_ignored(root, path):
            continue
        if path.resolve() in ignored_resolved:
            continue
        if path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        snapshot[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _tree_delta(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    before_keys = set(before)
    after_keys = set(after)
    return {
        "added": sorted(after_keys - before_keys),
        "removed": sorted(before_keys - after_keys),
        "modified": sorted(key for key in before_keys & after_keys if before[key] != after[key]),
    }


def _trim(text: str) -> str:
    if len(text) <= MAX_CAPTURE_CHARS:
        return text
    return f"[truncated {len(text) - MAX_CAPTURE_CHARS} chars]\n" + text[-MAX_CAPTURE_CHARS:]


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    """Terminate the test and all descendants in its process group."""

    if process.poll() is not None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=0.5)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        # Best effort on Windows.  CI runs on Linux, where the whole group is
        # guaranteed to be terminated above.
        try:
            process.kill()
        except OSError:
            pass


def run_invocation(
    spec: TestSpec,
    *,
    root: Path,
    hash_seed: int,
    report_path: Path | None = None,
) -> dict:
    before = _tree_snapshot(root, extra_ignored=(() if report_path is None else (report_path,)))
    started_at = _utc_now()
    started = time.monotonic()
    command = [sys.executable, "-X", "faulthandler", str(spec.path)]
    status = "error"
    returncode: int | None = None
    stdout = ""
    stderr = ""
    timeout_message = None
    process_group_terminated = False

    with tempfile.TemporaryDirectory(prefix=f"dds-test-{spec.test_id}-") as temp_dir:
        temp = Path(temp_dir)
        home = temp / "home"
        home.mkdir()
        env = os.environ.copy()
        env.update(
            {
                "HOME": str(home),
                "TMPDIR": str(temp),
                "TEMP": str(temp),
                "TMP": str(temp),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": str(hash_seed),
                "PYTHONPATH": str(root)
                + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""),
                "TZ": "UTC",
                "LC_ALL": "C.UTF-8",
                "LANG": "C.UTF-8",
                "DDS_TEST_MODE": "1",
            }
        )
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                command,
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=(os.name == "posix"),
            )
            try:
                stdout, stderr = process.communicate(timeout=spec.timeout_seconds)
                returncode = int(process.returncode)
                status = "passed" if returncode == 0 else "failed"
            except subprocess.TimeoutExpired:
                status = "timeout"
                timeout_message = f"Exceeded {spec.timeout_seconds:.3f} seconds"
                _terminate_process_group(process)
                process_group_terminated = True
                stdout, stderr = process.communicate()
                returncode = process.returncode
        except OSError as exc:
            status = "error"
            stderr = f"{type(exc).__name__}: {exc}"
            if process is not None:
                _terminate_process_group(process)

    duration = time.monotonic() - started
    after = _tree_snapshot(root, extra_ignored=(() if report_path is None else (report_path,)))
    mutation = _tree_delta(before, after)
    if any(mutation.values()):
        if status == "passed":
            status = "failed"
        stderr = (stderr + "\n" if stderr else "") + f"Source mutation detected: {mutation}"

    return {
        "test_id": spec.test_id,
        "suite": spec.suite,
        "path": spec.relative_path,
        "description": spec.description,
        "hash_seed": hash_seed,
        "status": status,
        "returncode": returncode,
        "started_at": started_at,
        "duration_seconds": round(duration, 6),
        "timeout_seconds": spec.timeout_seconds,
        "timeout_message": timeout_message,
        "process_group_terminated": process_group_terminated,
        "source_mutation": mutation,
        "command": command,
        "stdout": _trim(stdout),
        "stderr": _trim(stderr),
    }


def run_tests(
    specs: list[TestSpec],
    *,
    root: Path,
    selected_suites: set[str] | None = None,
    selected_ids: set[str] | None = None,
    fail_fast: bool = False,
    report_path: Path | None = None,
    manifest_digest: str | None = None,
) -> dict:
    known_ids = {spec.test_id for spec in specs}
    unknown_ids = sorted((selected_ids or set()) - known_ids)
    if unknown_ids:
        raise ManifestError(f"Unknown test ids: {unknown_ids}")
    known_suites = {spec.suite for spec in specs}
    unknown_suites = sorted((selected_suites or set()) - known_suites)
    if unknown_suites:
        raise ManifestError(f"Unknown test suites: {unknown_suites}")

    selected = [
        spec
        for spec in specs
        if (not selected_suites or spec.suite in selected_suites)
        and (not selected_ids or spec.test_id in selected_ids)
    ]
    if not selected:
        raise ManifestError("No tests matched the requested suite/id filters")

    started_at = _utc_now()
    results: list[dict] = []
    stopped_early = False
    for spec in selected:
        for seed in spec.hash_seeds:
            result = run_invocation(spec, root=root, hash_seed=seed, report_path=report_path)
            results.append(result)
            print(
                json.dumps(
                    {
                        "test": spec.test_id,
                        "suite": spec.suite,
                        "seed": seed,
                        "status": result["status"],
                        "duration_seconds": result["duration_seconds"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if fail_fast and result["status"] != "passed":
                stopped_early = True
                break
        if stopped_early:
            break

    counts = {
        status: sum(row["status"] == status for row in results)
        for status in ("passed", "failed", "timeout", "error")
    }
    failed = counts["failed"] + counts["timeout"] + counts["error"]
    return {
        "schema": REPORT_SCHEMA,
        "status": "ok" if failed == 0 and not stopped_early else "error",
        "started_at": started_at,
        "finished_at": _utc_now(),
        "root": str(root),
        "python": {
            "executable": sys.executable,
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "optimized": not __debug__,
        },
        "manifest_sha256": manifest_digest,
        "selected_suites": sorted(selected_suites or {spec.suite for spec in selected}),
        "selected_test_ids": [spec.test_id for spec in selected],
        "stopped_early": stopped_early,
        "summary": {
            "test_definitions": len(selected),
            "invocations": len(results),
            **counts,
        },
        "results": results,
    }


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Manifest-driven isolated DDS test runner")
    p.add_argument("--manifest", default="test_matrix.json")
    p.add_argument("--suite", action="append", default=[])
    p.add_argument("--test", action="append", default=[])
    p.add_argument("--report")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--check-only", action="store_true")
    p.add_argument("--list", action="store_true")
    return p


def main() -> None:
    if not __debug__:
        raise SystemExit(
            "DDS self-tests rely on assertions; optimized Python (-O/-OO) is forbidden"
        )
    args = parser().parse_args()
    manifest_path = Path(args.manifest).resolve()
    root = manifest_path.parent
    try:
        _, specs = load_manifest(manifest_path, root=root)
        digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        selected_suites = set(args.suite)
        selected_ids = set(args.test)
        if args.list:
            print(
                json.dumps(
                    [
                        {
                            "id": spec.test_id,
                            "suite": spec.suite,
                            "path": spec.relative_path,
                            "timeout_seconds": spec.timeout_seconds,
                            "hash_seeds": spec.hash_seeds,
                        }
                        for spec in specs
                    ],
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if args.check_only:
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "schema": MANIFEST_SCHEMA,
                        "tests": len(specs),
                        "suites": sorted({spec.suite for spec in specs}),
                        "manifest_sha256": digest,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        report_path = None if not args.report else Path(args.report).resolve()
        report = run_tests(
            specs,
            root=root,
            selected_suites=selected_suites or None,
            selected_ids=selected_ids or None,
            fail_fast=args.fail_fast,
            report_path=report_path,
            manifest_digest=digest,
        )
        if report_path is not None:
            _write_report(report_path, report)
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "summary": report["summary"],
                    "report": str(report_path) if report_path else None,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        if report["status"] != "ok":
            raise SystemExit(1)
    except ManifestError as exc:
        print(
            json.dumps(
                {"status": "manifest_error", "error": str(exc)},
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
