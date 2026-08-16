from __future__ import annotations

import json
import tempfile
from pathlib import Path

from test_runner import ManifestError, load_manifest, run_tests


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def manifest(rows: list[dict]) -> dict:
    return {
        "schema": "dds-test-manifest-v1",
        "default_timeout_seconds": 1,
        "default_hash_seeds": [0],
        "ignored_selftests": {},
        "tests": rows,
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "pass_selftest.py", "print('PASS')\n")
        write(root / "fail_selftest.py", "raise SystemExit(7)\n")
        write(root / "slow_selftest.py", "import time; time.sleep(2)\n")
        write(root / "mutate_selftest.py", "from pathlib import Path; Path('owned.py').write_text('changed')\n")
        write(root / "production.py", "VALUE = 1\n")

        rows = [
            {"id": "pass", "path": "pass_selftest.py", "suite": "fixture", "timeout_seconds": 3, "hash_seeds": [0, 123]},
            {"id": "fail", "path": "fail_selftest.py", "suite": "fixture", "timeout_seconds": 3},
            {"id": "slow", "path": "slow_selftest.py", "suite": "fixture", "timeout_seconds": 0.05},
            {"id": "mutate", "path": "mutate_selftest.py", "suite": "fixture", "timeout_seconds": 3},
        ]
        manifest_path = root / "test_matrix.json"
        primary_manifest = manifest(rows)
        primary_manifest["coverage"] = {"module_tests": {"production.py": ["pass"]}, "waivers": {}, "infrastructure": []}
        manifest_path.write_text(json.dumps(primary_manifest), encoding="utf-8")
        _, specs = load_manifest(manifest_path, root=root)
        report = run_tests(specs, root=root, selected_suites={"fixture"})
        statuses = [row["status"] for row in report["results"]]
        assert statuses.count("passed") == 2, statuses
        assert statuses.count("failed") == 2, statuses
        assert statuses.count("timeout") == 1, statuses
        mutation_row = next(row for row in report["results"] if row["test_id"] == "mutate")
        assert mutation_row["source_mutation"]["added"] == ["owned.py"], mutation_row
        assert report["status"] == "error"
        (root / "owned.py").unlink()

        orphan_rows = rows[:-1]
        orphan_manifest = manifest(orphan_rows)
        orphan_manifest["coverage"] = {"module_tests": {"production.py": ["pass"]}, "waivers": {}, "infrastructure": []}
        manifest_path.write_text(json.dumps(orphan_manifest), encoding="utf-8")
        try:
            load_manifest(manifest_path, root=root)
        except ManifestError as exc:
            assert "Unaccounted self-tests" in str(exc)
        else:
            raise AssertionError("Orphan self-test was not rejected")

        duplicate_rows = [rows[0], {**rows[1], "id": rows[0]["id"]}]
        # Account for the other discovered scripts explicitly as ignored with reasons.
        duplicate_manifest = manifest(duplicate_rows)
        duplicate_manifest["coverage"] = {"module_tests": {"production.py": ["pass"]}, "waivers": {}, "infrastructure": []}
        duplicate_manifest["ignored_selftests"] = {
            "slow_selftest.py": "fixture not relevant to duplicate-id validation",
            "mutate_selftest.py": "fixture not relevant to duplicate-id validation",
        }
        manifest_path.write_text(json.dumps(duplicate_manifest), encoding="utf-8")
        try:
            load_manifest(manifest_path, root=root)
        except ManifestError as exc:
            assert "Duplicate test id" in str(exc)
        else:
            raise AssertionError("Duplicate test id was not rejected")

        missing_coverage_manifest = manifest(rows)
        manifest_path.write_text(json.dumps(missing_coverage_manifest), encoding="utf-8")
        try:
            load_manifest(manifest_path, root=root)
        except ManifestError as exc:
            assert "coverage contract" in str(exc)
        else:
            raise AssertionError("Unmapped production module was not rejected")

        print(
            json.dumps(
                {
                    "ok": True,
                    "isolated_subprocesses": True,
                    "timeouts_detected": True,
                    "source_mutation_detected": True,
                    "orphan_selftest_detected": True,
                    "duplicate_manifest_entry_detected": True,
                    "hash_seed_matrix_exercised": True,
                    "production_coverage_contract_tested": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
