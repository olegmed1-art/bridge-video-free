"""Credential-free generic CI entrypoint; stdlib, fixtures only, no network calls."""
import ast
import hashlib
import json
import os
import sys
import stat
import tempfile
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
REPO = PACKAGE.parents[1]
EXPECTED_PACKAGE_FILES = {
    "hba_helper.py", "owner_collector.py", "test_hba_helper.py",
    "test_owner_collector.py", "process_supervisor.py", "run_ci.py",
    "README.md", "SOURCE_MANIFEST.json",
}
EXPECTED_TEST_CLASSES = ("test_hba_helper.NativeFixtureTests",
                         "test_owner_collector.CollectorTests")
NAMESPACE_METHODS = {
    "test_actual_directory_bind_visibility_if_namespace_available",
    "test_actual_file_bind_inode_pin_if_namespace_available",
    "test_namespace_timeout_kills_descendants_and_reaps_wrapper",
    "test_namespace_timeout_receipt_proves_descendant_drain",
}


def preflight():
    if sys.platform != "linux" or os.geteuid() == 0:
        raise RuntimeError("Existing non-root Linux required")
    if (set(os.environ) - {"PATH", "LANG", "LC_CTYPE"} or
            os.environ.get("PATH") != "/usr/bin:/bin" or os.environ.get("LANG") != "C"):
        raise RuntimeError("Unexpected environment keys; credentials must not be passed")
    # Interpreter audit only; no host firewall/security settings are changed.
    def no_network(event, args):
        if event.startswith("socket."):
            raise RuntimeError("Network operation refused in synthetic test interpreter")
    sys.addaudithook(no_network)
    # Validate the exact directory before any local module/test import.
    entries = list(PACKAGE.iterdir())
    if {entry.name for entry in entries} != EXPECTED_PACKAGE_FILES:
        raise RuntimeError("Exact generic directory inventory mismatch")
    for entry in entries:
        info = entry.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError("Generic source must be a regular single-link file")
    manifest = json.loads((PACKAGE / "SOURCE_MANIFEST.json").read_text())
    expected_pins = {"ci/hba-synthetic/" + name for name in EXPECTED_PACKAGE_FILES
                     if name != "SOURCE_MANIFEST.json"}
    expected_pins.add(".github/workflows/hba-synthetic-review.yml")
    if set(manifest["files"]) != expected_pins:
        raise RuntimeError("Exact manifest file inventory mismatch")
    if set(manifest["test_inventory"]) != set(EXPECTED_TEST_CLASSES):
        raise RuntimeError("Exact test-class inventory mismatch")
    found_cases = {}
    for name, pin in manifest["files"].items():
        data = (REPO / name).read_bytes()
        if len(data) != pin["bytes"] or hashlib.sha256(data).hexdigest() != pin["sha256"]:
            raise RuntimeError("Generic source hash mismatch: " + name)
        if name.endswith(".py"):
            tree = ast.parse(data, filename=name)
            if name in ("ci/hba-synthetic/test_hba_helper.py",
                         "ci/hba-synthetic/test_owner_collector.py"):
                module = Path(name).stem
                for node in tree.body:
                    if isinstance(node, ast.ClassDef):
                        key = module + "." + node.name
                        methods = sorted(child.name for child in node.body
                                         if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                                         and child.name.startswith("test_"))
                        if methods:
                            found_cases[key] = methods
            for node in ast.walk(tree):
                if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                        and any(isinstance(t, ast.Name) and t.id == "child" for t in node.targets)):
                    ast.parse(node.value.value, filename=name + ":embedded-child")
    if found_cases != manifest["test_inventory"]:
        raise RuntimeError("Exact test-method inventory mismatch before imports")
    if sum(map(len, found_cases.values())) != 58:
        raise RuntimeError("Unexpected pre-import method count")
    sys.path.insert(0, str(PACKAGE))


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main():
    preflight()
    mode = sys.argv[1] if len(sys.argv) == 2 else ""
    if mode not in ("ordinary", "namespaces"):
        raise RuntimeError("Select ordinary or namespaces")
    suite = unittest.defaultTestLoader.loadTestsFromNames(EXPECTED_TEST_CLASSES)
    selected = [test for test in flatten(suite)
                if (test._testMethodName in NAMESPACE_METHODS) == (mode == "namespaces")]
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected))
    # Keep SKIP separate, never convert it into a native capability PASS.
    status = ("FAIL" if not result.wasSuccessful() else
              "PASS_WITH_SKIPS" if result.skipped else "PASS")
    print(json.dumps({"scope": mode, "status": status, "tests_run": result.testsRun,
                      "passes": result.testsRun - len(result.skipped) - len(result.failures) - len(result.errors),
                      "skips_not_run": len(result.skipped), "failures": len(result.failures),
                      "errors": len(result.errors), "live_eligible": False}))
    if len(selected) != (54 if mode == "ordinary" else 4):
        raise RuntimeError("Unexpected test method inventory")
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
