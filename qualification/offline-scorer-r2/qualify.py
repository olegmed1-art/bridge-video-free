#!/usr/bin/env python3
"""Linux synthetic qualification only. No real-media or production evaluation."""
from __future__ import annotations
import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import signal
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

VERSIONS = {"pytest": "8.4.1", "iniconfig": "2.1.0", "packaging": "25.0",
            "pluggy": "1.6.0", "pygments": "2.20.0"}
EXPECTED_SOURCE = "2343d67f7f5b2e2631f4c6bf8d6845187113b68b9da54147eb6ffa204fb9db4b"
EXPECTED_TEST = "90b7831647aee36f77aaf855e21e4d7f7083a6660cad7dabcf0db7ea8c9db97f"

def require(value, message):
    if not value:
        raise RuntimeError(message)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def timeout_handler(signum, frame):
    raise TimeoutError("synthetic qualification wall time exceeded")

class AuditGuard:
    """A caught rejection remains a qualification failure."""
    def __init__(self):
        self.denied_count = 0
        self.denied_events = []
    def audit(self, event, args):
        if event.startswith(("socket.", "subprocess.")) or event in {
                "os.system", "os.posix_spawn", "os.spawn", "os.exec", "os.fork", "os.forkpty"}:
            self.denied_count += 1
            if len(self.denied_events) < 16:
                self.denied_events.append(event)  # Names only; never arguments.
            raise RuntimeError("qualification refused external I/O: " + event)
    def require_clean(self):
        require(self.denied_count == 0, "qualification observed prohibited I/O attempts")

class QualificationControls:
    def __init__(self):
        self.nodeids, self.passed, self.problems = [], set(), []
    def pytest_collection_modifyitems(self, items):
        self.nodeids = [item.nodeid for item in items]
        require(len(self.nodeids) == 68 and len(set(self.nodeids)) == 68,
                "exactly 68 unique collected controls required")
    def pytest_runtest_logreport(self, report):
        if report.failed or report.skipped or hasattr(report, "wasxfail"):
            self.problems.append({"nodeid": report.nodeid, "phase": report.when,
                                  "outcome": report.outcome})
        if report.when == "call" and report.passed:
            self.passed.add(report.nodeid)
    def require_clean(self, code):
        require(code == 0 and len(self.passed) == 68 and not self.problems,
                "failed/errored/skipped/xfail/incomplete synthetic controls")

def privilege_boundary():
    fields = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip()
    require(fields.get("NoNewPrivs") == "1", "NoNewPrivs=1 is mandatory")
    for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        value = fields.get(name)
        require(isinstance(value, str) and value and
                all(c in "0123456789abcdefABCDEF" for c in value) and int(value, 16) == 0,
                "zero " + name + " is mandatory")
    for name in ("Uid", "Gid"):
        ids = fields.get(name, "").split()
        require(len(ids) == 4 and all(x.isdecimal() for x in ids) and
                len(set(ids)) == 1 and int(ids[0]) > 0,
                "all real/effective/saved/filesystem IDs must be equal and non-root: " + name)
    require(not fields.get("Groups", "") and not os.getgroups(),
            "supplementary groups must be empty")
    return {"no_new_privs": True, "capability_sets_zero": True,
            "nonroot_ids_verified": True, "supplementary_groups_empty": True}

PROJECT_FILES = {
    "tools/comparison_score_handoff.py", "tools/comparison_score_coordinator.py",
    "tests/test_comparison_score_handoff.py", "tests/test_comparison_score_coordinator.py",
}

def verify_project_sources(root, manifest):
    project = root.parent.parent
    rows = manifest["project_source_pins"]
    require(len(rows) == 4 and {r["path"] for r in rows} == PROJECT_FILES,
            "exact four project source pins required")
    actual = set()
    for name in ("tools", "tests"):
        directory = project / name
        require(directory.is_dir() and not directory.is_symlink(), "regular project source directory")
        for path in directory.rglob("*"):
            require(not path.is_symlink() and (path.is_dir() or path.is_file()),
                    "project source entries must be regular")
            if path.is_file():
                actual.add(path.relative_to(project).as_posix())
    require(actual == PROJECT_FILES, "project source inventory missing/extra files")
    for row in rows:
        path = project / row["path"]
        data = path.read_bytes()
        require(type(row["bytes"]) is int and row["bytes"] >= 0
                and len(data) == row["bytes"] and digest(data) == row["sha256"],
                "project source bytes changed: " + row["path"])
        compile(data, str(path), "exec", dont_inherit=True)

def run_project_suite(path, name, expected):
    import unittest
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    def ids(group):
        for test in group:
            if isinstance(test, unittest.TestSuite):
                yield from ids(test)
            else:
                yield test.id()
    collected = list(ids(suite))
    require(len(collected) == expected and len(set(collected)) == expected,
            "exact unique project suite collection required: " + name)
    checks = unittest.TextTestRunner(verbosity=1).run(suite)
    require(checks.testsRun == expected and checks.wasSuccessful()
            and not checks.skipped and not checks.expectedFailures and not checks.unexpectedSuccesses,
            "all project suite controls must execute and pass: " + name)
    return {"expected": expected, "passed": checks.testsRun,
            "skipped": 0, "failed": 0, "errors": 0, "xfail": 0}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--parent-netns", required=True)
    args = parser.parse_args()
    require(sys.platform == "linux" and platform.python_implementation() == "CPython" and
            sys.version_info[:3] == (3, 12, 10), "Linux CPython 3.12.10 required")
    require(os.geteuid() != 0, "qualification must run as an unprivileged user")
    boundary = privilege_boundary()
    require(os.readlink("/proc/self/ns/net") != args.parent_netns,
            "a separate Linux network namespace is required")
    require(len(Path("/proc/net/route").read_text().splitlines()) <= 1,
            "isolated namespace must have no IPv4 routes")
    require(sys.flags.isolated and sys.dont_write_bytecode, "invoke Python with -I -B")
    for name, value in VERSIONS.items():
        require(importlib.metadata.version(name) == value, "dependency version mismatch: " + name)
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024**2, 16 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_NPROC, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)
    started = time.monotonic()
    root = Path(__file__).resolve().parent
    results = args.results.resolve()
    require(not results.is_relative_to(root), "results must be outside immutable qualification sources")
    require(not results.exists(), "results directory must be new")
    results.mkdir(mode=0o700, parents=True)
    os.umask(0o077)
    # No inherited credentials, cloud settings, PYTHONPATH or pytest plugin state.
    os.environ.clear()
    os.environ.update(HOME=str(results), TMPDIR=str(results),
                      PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1")
    guard = AuditGuard()
    sys.addaudithook(guard.audit)
    manifest_bytes = (root / "MANIFEST.json").read_bytes()
    require(digest(manifest_bytes) == args.manifest_sha256, "out-of-band manifest pin mismatch")
    manifest = json.loads(manifest_bytes)
    require(manifest["schema"] == "generic-scorer-r2-synthetic-qualification/v1"
            and manifest["qualification_revision"] == "v3-coordinator-synthetic"
            and manifest["expected_cases"] == 68
            and manifest["harness_guard_regression_cases"] == 4
            and manifest["project_suite_cases"] == {"integration": 18, "coordinator": 19},
            "qualification manifest schema/support")
    verify_project_sources(root, manifest)
    declared = {}
    for item in manifest["files"]:
        key = item["path"]
        require(isinstance(key, str) and key and "\\" not in key and ":" not in key
                and not key.startswith("/") and
                all(p not in {"", ".", ".."} for p in key.split("/")) and key not in declared,
                "invalid manifest path")
        declared[key] = item
    actual = set()
    for path in root.rglob("*"):
        require(not path.is_symlink(), "qualification source symlink rejected")
        require(path.is_dir() or path.is_file(), "qualification source must be regular")
        if path.is_file() and path != root / "MANIFEST.json":
            actual.add(path.relative_to(root).as_posix())
    require(actual == set(declared), "source inventory missing/extra files")
    snapshots = {}
    for key, item in declared.items():
        path = (root / key).resolve(strict=True)
        require(path.is_relative_to(root) and path.is_file(), "qualification path escape")
        data = path.read_bytes()
        require(len(data) == item["bytes"] and digest(data) == item["sha256"],
                "qualification source bytes changed: " + key)
        snapshots[key] = data
        if path.suffix == ".py":
            compile(data, str(path), "exec", dont_inherit=True)
    require(digest(snapshots["offline_score.py"]) == EXPECTED_SOURCE and
            digest(snapshots["test_offline_score.py"]) == EXPECTED_TEST,
            "reviewed scorer/public-test-copy identity mismatch")
    api_root = root / "api"
    sys.path.insert(0, str(api_root))
    for item in manifest["api_source_pins"]:
        module_name = item["path"].removesuffix(".py").replace("/", ".")
        if module_name.endswith(".__init__"):
            module_name = module_name.removesuffix(".__init__")
        module = importlib.import_module(module_name)
        require(Path(module.__file__).resolve() == api_root / item["path"],
                "API import resolved outside pinned source closure")
    import pytest

    # Separate guard regressions do not alter the 68 scorer cases or their JUnit.
    import unittest
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location("harness_guard_regressions",
                                                root / "test_harness_guards.py")
    regressions = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(regressions)
    suite = unittest.defaultTestLoader.loadTestsFromModule(regressions)
    checks = unittest.TextTestRunner(verbosity=1).run(suite)
    require(checks.testsRun == 4 and checks.wasSuccessful() and
            not checks.skipped and not checks.expectedFailures and not checks.unexpectedSuccesses,
            "all four harness guard regressions must execute and pass")
    guard.require_clean()
    controls = QualificationControls()
    # Explicit private base avoids shared /tmp/pytest-of-* state.
    with tempfile.TemporaryDirectory(prefix="synthetic-", dir=results) as private_tmp:
        os.chmod(private_tmp, 0o700)
        # Same process, audit hook, network namespace and resource limits as scorer.
        os.environ["TMPDIR"] = private_tmp
        tempfile.tempdir = private_tmp
        project = root.parent.parent
        project_checks = {}
        for name, expected in (("integration", 18), ("coordinator", 19)):
            filename = ("test_comparison_score_handoff.py" if name == "integration"
                        else "test_comparison_score_coordinator.py")
            project_checks[name] = run_project_suite(
                project / "tests" / filename, "qualified_" + name + "_controls", expected)
            guard.require_clean()
        xml_path = results / "synthetic.xml"
        code = pytest.main(["-q", "-ra", "-p", "no:cacheprovider", "--strict-markers",
                            "--strict-config", "-W", "error", "-c", str(root / "pytest.ini"),
                            "--rootdir", str(root), "--basetemp", str(Path(private_tmp) / "cases"),
                            "--junitxml", str(xml_path), str(root / "test_offline_score.py")],
                           plugins=[controls])
        temp_bytes = sum(p.stat().st_size for p in Path(private_tmp).rglob("*") if p.is_file())
        require(temp_bytes <= 128 * 1024**2, "synthetic temporary disk budget exceeded")
        controls.require_clean(code)
        guard.require_clean()
        cases = list(ET.parse(xml_path).iter("testcase"))
        require(len(cases) == 68 and not any(c.find(tag) is not None
                for c in cases for tag in ("skipped", "failure", "error")), "JUnit qualification failed")
    verify_project_sources(root, manifest)
    # Revalidate source bytes after pytest and imports.
    for key, item in declared.items():
        require(digest((root / key).read_bytes()) == item["sha256"], "sources changed during qualification")
    require(digest((root / "MANIFEST.json").read_bytes()) == args.manifest_sha256,
            "manifest changed during qualification")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
             and p != root / "MANIFEST.json"} == set(declared), "post-run inventory changed")
    guard.require_clean()
    boundary = privilege_boundary()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    summary = {"schema": "generic-scorer-r2-qualification-receipt/v1", "status": "PASS",
               "dataset": "SYNTHETIC_ONLY", "expected_cases": 68, "passed": 68,
               "network_namespace_distinct": True, "python_external_io_guard": True,
               "denied_io_events": guard.denied_count, "harness_guard_controls": 4,
               "project_suite_controls": project_checks, "total_controls": 109,
               "privilege_boundary": boundary,
               "skipped": 0, "failed": 0, "errors": 0, "xfail": 0,
               "manifest_sha256": args.manifest_sha256, "python": platform.python_version(),
               "dependencies": VERSIONS, "wall_seconds": time.monotonic() - started,
               "cpu_seconds": usage.ru_utime + usage.ru_stime, "peak_rss_kib": usage.ru_maxrss,
               "temporary_bytes_before_cleanup": temp_bytes,
               "real_video_accuracy_evaluated": False, "promotion_allowed": False}
    (results / "qualification.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
    print(json.dumps(summary, sort_keys=True))
    signal.alarm(0)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
