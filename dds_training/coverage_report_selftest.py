from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from coverage_report import CoverageError, make_report


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="dds-coverage-selftest-") as td:
        root = Path(td)
        module = root / "module.py"
        module.write_text(
            "def choose(value):\n"
            "    if value:\n"
            "        return 1\n"
            "    return 0\n",
            encoding="utf-8",
        )
        manifest = {
            "tests": [{"id": "t", "suite": "fast"}],
            "coverage": {
                "module_tests": {"module.py": ["t"]},
                "runtime_coverage": {
                    "fast": {
                        "minimum_overall_percent": 50,
                        "minimum_module_percent": 50,
                        "minimum_module_execution_ratio": 1.0,
                    }
                },
            },
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        fragments = root / "fragments"
        fragments.mkdir()
        (fragments / "coverage-1.json").write_text(json.dumps({
            "schema": "dds-runtime-coverage-fragment-v1",
            "pid": 1,
            "root": str(root),
            "lines": {"module.py": [1, 2, 3]},
            "arcs": {"module.py": [[-1, 1], [1, 2], [2, 3], [3, -1]]},
        }), encoding="utf-8")
        report = make_report(root, manifest_path, fragments, "fast")
        assert report["status"] == "ok", report
        assert report["summary"]["modules"] == 1
        assert report["summary"]["executed_modules"] == 1
        assert report["modules"]["module.py"]["line_percent"] >= 50
        assert report["modules"]["module.py"]["executed_arcs"] == 4

        valid_fragment = json.loads((fragments / "coverage-1.json").read_text())
        malformed_fragments = [
            [],
            {**valid_fragment, "lines": []},
            {**valid_fragment, "arcs": []},
            {**valid_fragment, "lines": {"module.py": [True, 2, 3]}},
            {**valid_fragment, "lines": {"module.py": [1.9, 2, 3]}},
            {**valid_fragment, "lines": {"module.py": "123"}},
            {**valid_fragment, "arcs": {"module.py": [[1]]}},
            {**valid_fragment, "arcs": {"module.py": [[1, 2, 3]]}},
            {**valid_fragment, "arcs": {"module.py": [[True, 2]]}},
            {**valid_fragment, "arcs": {"module.py": [[1, None]]}},
        ]
        output = root / "report.json"
        for malformed in malformed_fragments:
            (fragments / "coverage-1.json").write_text(json.dumps(malformed))
            output.write_text(json.dumps(report))
            process = subprocess.run([
                sys.executable, str(Path(__file__).with_name("coverage_report.py")),
                "--root", str(root), "--manifest", str(manifest_path),
                "--fragments", str(fragments), "--suite", "fast",
                "--out", str(output), "--fail-on-error",
            ], capture_output=True, text=True)
            assert process.returncode == 1, (malformed, process.stdout, process.stderr)
            rejected = json.loads(output.read_text())
            assert rejected["status"] == "error", (malformed, rejected, process.stderr)
            assert rejected["findings"][0]["code"] == "COVERAGE_REPORT_ERROR", rejected
            assert "Traceback" not in process.stderr, process.stderr
        (fragments / "coverage-1.json").write_text(json.dumps(valid_fragment))

        manifest["coverage"]["runtime_coverage"]["fast"]["minimum_overall_percent"] = 100
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        blocked = make_report(root, manifest_path, fragments, "fast")
        assert blocked["status"] == "error"
        assert any(row["code"] == "OVERALL_COVERAGE_BELOW_MINIMUM" for row in blocked["findings"])

        empty = root / "empty"
        empty.mkdir()
        try:
            make_report(root, manifest_path, empty, "fast")
        except Exception as exc:
            assert "No runtime coverage fragments" in str(exc)
        else:
            raise AssertionError("Missing coverage fragments were accepted")

        for mapping in ({}, {"module.py": ["other-suite-test"]}):
            manifest["coverage"]["module_tests"] = mapping
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            try:
                make_report(root, manifest_path, fragments, "fast")
            except CoverageError as exc:
                assert "No modules are mapped" in str(exc), exc
            else:
                raise AssertionError("An unmapped suite was reported as fully covered")

        manifest["coverage"]["module_tests"] = {"module.py": ["t"]}
        threshold_keys = ("minimum_overall_percent", "minimum_module_percent", "minimum_module_execution_ratio")
        for key in threshold_keys:
            maximum = 1 if key.endswith("ratio") else 100
            for bad in ("NaN", "Infinity", -1, maximum + 1, True, None, "invalid"):
                manifest["coverage"]["runtime_coverage"]["fast"] = {key: bad}
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                try:
                    make_report(root, manifest_path, fragments, "fast")
                except CoverageError as exc:
                    assert key in str(exc), exc
                else:
                    raise AssertionError(f"Invalid threshold accepted: {key}={bad!r}")

        print(json.dumps({
            "ok": True,
            "line_coverage_measured": True,
            "arc_coverage_measured": True,
            "minimum_threshold_enforced": True,
            "missing_fragments_blocked": True,
        }, indent=2))


if __name__ == "__main__":
    main()
