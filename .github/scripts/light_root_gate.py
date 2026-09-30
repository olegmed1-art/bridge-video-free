"""Fail-closed, offline source-test gate. No production acceptance is implied."""
import fcntl
import importlib.metadata
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


def require(value, message):
    if not value:
        raise RuntimeError(message)


def preflight():
    require(sys.platform == "linux", "Linux required")
    require(os.getuid() == os.geteuid() == os.getgid() == os.getegid() == 0,
            "Real UID/GID 0 required; never alter the tests' root guards")
    require(sys.version_info[:2] == (3, 12), "Python 3.12 required")
    require(Path.cwd() == Path('/work/candidate'), "Run from /work/candidate")
    require(os.environ.get('PYTEST_DISABLE_PLUGIN_AUTOLOAD') == '1',
            "Disable external pytest plugins")
    require('PYTEST_ADDOPTS' not in os.environ and 'PYTEST_PLUGINS' not in os.environ,
            "Unexpected pytest environment overrides")
    work = Path('/work').stat()
    require(work.st_uid == work.st_gid == 0 and stat.S_IMODE(work.st_mode) == 0o755,
            "/work must be root:root 0755")
    for name in ('light_native_lane_cycle.py', 'light_native_lane_issuer.py'):
        require((Path('/work/baseline/ops') / name).is_file(),
                "Missing immutable baseline source: " + name)
    for name in ('light_native_lane_run_guard.py', 'light_native_pilot_run_guard.py'):
        require((Path('/work/candidate/ops') / name).is_file(),
                "Incomplete candidate source package: " + name)
    for package, version in (("pytest", "8.4.2"), ("psycopg", "3.3.4"),
                             ("psycopg-binary", "3.3.4")):
        require(importlib.metadata.version(package) == version, package)
    import typing_extensions  # Required by the real isolated-child test.
    require(set(os.listdir("/sys/class/net")) == {"lo"},
            "Expected isolated network namespace with loopback only")
    with tempfile.TemporaryDirectory(dir='/var/tmp') as directory:
        root = Path(directory)
        path = root / "record"
        with path.open("wb") as stream:
            # EPERM is a hard failure: never retry with broader capabilities.
            os.fchown(stream.fileno(), 0, 0)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(b"root-fsync-proof")
            stream.flush()
            os.fsync(stream.fileno())
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            probe = subprocess.run([sys.executable, "-c",
                "import fcntl,sys; f=open(sys.argv[1],'rb'); "
                "fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)", str(path)],
                capture_output=True, timeout=10)
            require(probe.returncode != 0 and b"BlockingIOError" in probe.stderr,
                    "Real cross-process flock exclusion required")
        os.chown(path, 0, 0)
        path.chmod(0o600)
        record = path.stat()
        require(record.st_uid == record.st_gid == 0
                and stat.S_IMODE(record.st_mode) == 0o600,
                "Root:root 0600 files required")
        (root / "link").symlink_to(path)
        require((root / "link").is_symlink(), "Real symlink required")
        directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    print("PREFLIGHT PASS: Linux, real UID/GID 0, baseline/candidate, "
          "Python/dependencies, chown/fchown/chmod/flock/fsync/symlink", flush=True)


def run_suite(label, selectors, expected=None):
    report = Path("/tmp") / (label + ".xml")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-ra",
         "-p", "no:cacheprovider", "--noconftest", "-c", "/dev/null",
         "--rootdir=/work/candidate", "--junitxml=" + str(report), *selectors],
        cwd="/work/candidate", timeout=240, check=False)
    require(result.returncode == 0, label + ": pytest failed")
    cases = ET.parse(report).getroot().findall(".//testcase")
    require(bool(cases), label + ": no test cases executed")
    require(all(not any(case.find(tag) is not None
                        for tag in ("skipped", "failure", "error")) for case in cases),
            label + ": skips, xfails, failures or errors are not evidence")
    if expected is not None:
        require(len(cases) == expected, label + ": unexpected case count")
    print(f"{label}: {len(cases)} passed, zero skipped", flush=True)


if __name__ == "__main__":
    preflight()
    run_suite("new-root-cases", [
        "tests/test_light_native_lane_cycle.py::test_cycle_retains_only_bounded_original_refusal",
        "tests/test_light_native_lane_cycle.py::test_unknown_cycle_refusal_never_exposes_exception_text",
        "tests/test_light_native_lane_issuer.py::test_incomplete_refusal_diagnostic_is_optional_and_bound",
    ], expected=8)
    run_suite("full-cycle-issuer", [
        "tests/test_light_native_lane_cycle.py", "tests/test_light_native_lane_issuer.py",
    ], expected=86)
    run_suite("stdlib-diagnostics", [
        "tests/test_light_native_lane_diagnostics_stdlib.py",
    ], expected=14)
    # The first 8 cases are included in the full set of 86, not additional cases.
    print("SOURCE TEST GATE PASS: 100 unique cases (86 + 14); "
          "8 focused cases repeated; no production clearance", flush=True)
