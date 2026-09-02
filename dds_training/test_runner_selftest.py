from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from test_runner import ManifestError, load_manifest, run_tests


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def manifest(rows: list[dict]) -> dict:
    return {
        "schema": "dds-test-manifest-v1",
        "default_timeout_seconds": 1,
        "default_hash_seeds": [0],
        "ignored_selftests": {},
        "tests": rows,
        "coverage": {
            "max_waivers": 0,
            "module_tests": {"production.py": ["pass"]},
            "waivers": {},
            "infrastructure": [],
        },
    }


def expect_manifest_error(
    manifest_path: Path,
    root: Path,
    expected: str,
    *,
    enforce_core: bool = False,
) -> None:
    try:
        load_manifest(manifest_path, root=root, enforce_core=enforce_core)
    except ManifestError as exc:
        assert expected in str(exc), (expected, str(exc))
    else:
        raise AssertionError(f"Manifest error was not raised: {expected}")


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        write(root / "pass_selftest.py", "print('PASS')\n")
        write(root / "fail_selftest.py", "raise SystemExit(7)\n")
        write(root / "slow_selftest.py", "import time; time.sleep(2)\n")
        write(
            root / "child_slow_selftest.py",
            (
                "import subprocess, sys, time\n"
                "subprocess.Popen([sys.executable, '-c', "
                "\"import time; time.sleep(0.4); "
                "open('escaped.py','w').write('escaped')\"])\n"
                "time.sleep(2)\n"
            ),
        )
        write(
            root / "mutate_selftest.py",
            "from pathlib import Path; Path('owned.py').write_text('changed')\n",
        )
        write(root / "production.py", "VALUE = 1\n")

        rows = [
            {
                "id": "pass",
                "path": "pass_selftest.py",
                "suite": "fixture",
                "timeout_seconds": 3,
                "hash_seeds": [0, 123],
            },
            {
                "id": "fail",
                "path": "fail_selftest.py",
                "suite": "fixture",
                "timeout_seconds": 3,
            },
            {
                "id": "slow",
                "path": "slow_selftest.py",
                "suite": "fixture",
                "timeout_seconds": 0.05,
            },
            {
                "id": "child-slow",
                "path": "child_slow_selftest.py",
                "suite": "fixture",
                "timeout_seconds": 0.05,
            },
            {
                "id": "mutate",
                "path": "mutate_selftest.py",
                "suite": "fixture",
                "timeout_seconds": 3,
            },
        ]
        manifest_path = root / "test_matrix.json"
        primary_manifest = manifest(rows)
        manifest_path.write_text(json.dumps(primary_manifest), encoding="utf-8")

        # Fixture manifests deliberately do not contain the production core
        # tests, so the test-only escape hatch must be explicit.  Use valid
        # production suite names here to reach the hard-coded core check.
        strict_manifest = manifest(
            [
                {
                    **row,
                    "suite": "fast",
                    "description": f"fixture {row['id']}",
                }
                for row in rows
            ]
        )
        manifest_path.write_text(json.dumps(strict_manifest), encoding="utf-8")
        expect_manifest_error(
            manifest_path,
            root,
            "Required core test is missing",
            enforce_core=True,
        )
        manifest_path.write_text(json.dumps(primary_manifest), encoding="utf-8")
        _, specs = load_manifest(manifest_path, root=root, enforce_core=False)
        report = run_tests(specs, root=root, selected_suites={"fixture"})
        statuses = [row["status"] for row in report["results"]]
        assert statuses.count("passed") == 2, statuses
        assert statuses.count("failed") == 2, statuses
        assert statuses.count("timeout") == 2, statuses
        timeout_rows = [row for row in report["results"] if row["status"] == "timeout"]
        assert all(row["process_group_terminated"] is True for row in timeout_rows), timeout_rows
        mutation_row = next(row for row in report["results"] if row["test_id"] == "mutate")
        assert mutation_row["source_mutation"]["added"] == ["owned.py"], mutation_row
        assert report["status"] == "error"
        (root / "owned.py").unlink()

        # The child process must not outlive a timed-out test and mutate the
        # source tree after the runner has already returned.
        time.sleep(0.7)
        assert not (root / "escaped.py").exists(), "Timed-out child escaped its process group"

        orphan_manifest = manifest(rows)
        manifest_path.write_text(json.dumps(orphan_manifest), encoding="utf-8")
        write(root / "nested" / "orphan_selftest.py", "print('orphan')\n")
        expect_manifest_error(manifest_path, root, "Unaccounted self-tests")
        (root / "nested" / "orphan_selftest.py").unlink()
        (root / "nested").rmdir()

        duplicate_rows = [rows[0], {**rows[1], "id": rows[0]["id"]}]
        duplicate_manifest = manifest(duplicate_rows)
        duplicate_manifest["ignored_selftests"] = {
            "slow_selftest.py": "fixture not relevant to duplicate-id validation",
            "child_slow_selftest.py": "fixture not relevant to duplicate-id validation",
            "mutate_selftest.py": "fixture not relevant to duplicate-id validation",
        }
        manifest_path.write_text(json.dumps(duplicate_manifest), encoding="utf-8")
        expect_manifest_error(manifest_path, root, "Duplicate test id")

        missing_coverage_manifest = manifest(rows)
        del missing_coverage_manifest["coverage"]
        manifest_path.write_text(json.dumps(missing_coverage_manifest), encoding="utf-8")
        expect_manifest_error(manifest_path, root, "coverage contract")

        waiver_manifest = manifest(rows)
        waiver_manifest["coverage"] = {
            "max_waivers": 0,
            "module_tests": {},
            "waivers": {"production.py": "temporary fixture waiver"},
            "infrastructure": [],
        }
        manifest_path.write_text(json.dumps(waiver_manifest), encoding="utf-8")
        expect_manifest_error(manifest_path, root, "waiver budget exceeded")

        print(
            json.dumps(
                {
                    "ok": True,
                    "isolated_subprocesses": True,
                    "timeouts_detected": True,
                    "descendant_processes_terminated": True,
                    "source_mutation_detected": True,
                    "recursive_orphan_selftest_detected": True,
                    "duplicate_manifest_entry_detected": True,
                    "required_core_contract_tested": True,
                    "hash_seed_matrix_exercised": True,
                    "production_coverage_contract_tested": True,
                    "coverage_waiver_budget_tested": True,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
