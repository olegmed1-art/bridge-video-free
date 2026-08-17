from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable


class CoverageError(RuntimeError):
    pass


def executable_lines(path: Path) -> set[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.stmt, ast.ExceptHandler)) and getattr(node, "lineno", None):
            lines.add(int(node.lineno))
    return lines


def branch_lines(path: Path) -> set[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    branch_types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.Match, ast.IfExp)
    return {
        int(node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, branch_types) and getattr(node, "lineno", None)
    }


def load_fragments(directory: Path) -> tuple[dict[str, set[int]], dict[str, set[tuple[int, int]]], list[str]]:
    lines: dict[str, set[int]] = defaultdict(set)
    arcs: dict[str, set[tuple[int, int]]] = defaultdict(set)
    files = []
    for path in sorted(directory.glob("coverage-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CoverageError(f"Cannot read coverage fragment {path}: {exc}") from exc
        if data.get("schema") != "dds-runtime-coverage-fragment-v1":
            raise CoverageError(f"Unexpected coverage fragment schema in {path}")
        files.append(path.name)
        for module, values in data.get("lines", {}).items():
            lines[module].update(int(value) for value in values)
        for module, values in data.get("arcs", {}).items():
            arcs[module].update((int(value[0]), int(value[1])) for value in values)
    if not files:
        raise CoverageError(f"No runtime coverage fragments found in {directory}")
    return lines, arcs, files


def selected_modules(manifest: dict, suite: str) -> tuple[list[str], list[str]]:
    tests = manifest.get("tests", [])
    suite_ids = {str(row["id"]) for row in tests if row.get("suite") == suite}
    if not suite_ids:
        raise CoverageError(f"No tests are registered for suite {suite!r}")
    coverage = manifest.get("coverage", {})
    mapped = []
    for module, test_ids in coverage.get("module_tests", {}).items():
        if suite_ids.intersection(str(value) for value in test_ids):
            mapped.append(str(module))
    return sorted(mapped), sorted(suite_ids)


def make_report(root: Path, manifest_path: Path, fragments: Path, suite: str) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    modules, suite_ids = selected_modules(manifest, suite)
    covered_lines, covered_arcs, fragment_files = load_fragments(fragments)
    thresholds = manifest.get("coverage", {}).get("runtime_coverage", {}).get(suite, {})
    minimum_overall = float(thresholds.get("minimum_overall_percent", 0.0))
    minimum_module = float(thresholds.get("minimum_module_percent", 0.0))
    minimum_execution_ratio = float(thresholds.get("minimum_module_execution_ratio", 0.0))

    module_reports = {}
    total_executable = 0
    total_executed = 0
    executed_modules = 0
    branch_sites_total = 0
    branch_sites_observed = 0
    findings = []

    for module in modules:
        path = root / module
        if not path.is_file():
            raise CoverageError(f"Mapped production module is missing: {module}")
        executable = executable_lines(path)
        executed = covered_lines.get(module, set()) & executable
        arcs = covered_arcs.get(module, set())
        sites = branch_lines(path)
        observed_sites = {
            line for line in sites
            if len({end for start, end in arcs if start == line}) >= 1
        }
        percent = 100.0 if not executable else 100.0 * len(executed) / len(executable)
        if executed:
            executed_modules += 1
        if percent + 1e-12 < minimum_module:
            findings.append({
                "code": "MODULE_COVERAGE_BELOW_MINIMUM",
                "module": module,
                "actual_percent": round(percent, 3),
                "minimum_percent": minimum_module,
            })
        total_executable += len(executable)
        total_executed += len(executed)
        branch_sites_total += len(sites)
        branch_sites_observed += len(observed_sites)
        module_reports[module] = {
            "executable_lines": len(executable),
            "executed_lines": len(executed),
            "line_percent": round(percent, 3),
            "missing_lines": sorted(executable - executed),
            "executed_arcs": len(arcs),
            "branch_sites": len(sites),
            "observed_branch_sites": len(observed_sites),
            "unobserved_branch_lines": sorted(sites - observed_sites),
        }

    overall = 100.0 if not total_executable else 100.0 * total_executed / total_executable
    execution_ratio = 1.0 if not modules else executed_modules / len(modules)
    if overall + 1e-12 < minimum_overall:
        findings.append({
            "code": "OVERALL_COVERAGE_BELOW_MINIMUM",
            "actual_percent": round(overall, 3),
            "minimum_percent": minimum_overall,
        })
    if execution_ratio + 1e-12 < minimum_execution_ratio:
        findings.append({
            "code": "MODULE_EXECUTION_RATIO_BELOW_MINIMUM",
            "actual_ratio": round(execution_ratio, 4),
            "minimum_ratio": minimum_execution_ratio,
            "unexecuted_modules": sorted(module for module, row in module_reports.items() if row["executed_lines"] == 0),
        })

    return {
        "schema": "dds-runtime-coverage-report-v1",
        "status": "ok" if not findings else "error",
        "suite": suite,
        "suite_test_ids": suite_ids,
        "fragment_count": len(fragment_files),
        "fragment_files": fragment_files,
        "thresholds": {
            "minimum_overall_percent": minimum_overall,
            "minimum_module_percent": minimum_module,
            "minimum_module_execution_ratio": minimum_execution_ratio,
        },
        "summary": {
            "modules": len(modules),
            "executed_modules": executed_modules,
            "module_execution_ratio": round(execution_ratio, 4),
            "executable_lines": total_executable,
            "executed_lines": total_executed,
            "line_percent": round(overall, 3),
            "branch_sites": branch_sites_total,
            "observed_branch_sites": branch_sites_observed,
            "executed_arcs": sum(len(values) for values in covered_arcs.values()),
        },
        "modules": module_reports,
        "findings": findings,
    }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Merge dependency-free runtime coverage fragments")
    p.add_argument("--root", default=".")
    p.add_argument("--manifest", required=True)
    p.add_argument("--fragments", required=True)
    p.add_argument("--suite", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--fail-on-error", action="store_true")
    return p


def main() -> None:
    args = parser().parse_args()
    try:
        report = make_report(
            Path(args.root).resolve(),
            Path(args.manifest).resolve(),
            Path(args.fragments).resolve(),
            args.suite,
        )
    except (CoverageError, OSError, json.JSONDecodeError, SyntaxError) as exc:
        report = {
            "schema": "dds-runtime-coverage-report-v1",
            "status": "error",
            "suite": args.suite,
            "findings": [{"code": "COVERAGE_REPORT_ERROR", "detail": str(exc)}],
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.fail_on_error and report["status"] != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
