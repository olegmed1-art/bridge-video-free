from __future__ import annotations

import json
import tempfile
from pathlib import Path

from coverage_report import make_report


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

        print(json.dumps({
            "ok": True,
            "line_coverage_measured": True,
            "arc_coverage_measured": True,
            "minimum_threshold_enforced": True,
            "missing_fragments_blocked": True,
        }, indent=2))


if __name__ == "__main__":
    main()
